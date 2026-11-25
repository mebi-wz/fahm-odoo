from odoo import api, fields, models
from odoo.exceptions import UserError

LINE_TYPES = [
    ("bottle", "Small Bottle Line (PET)"),
    ("jar", "20 L Jar Line"),
]


class FahmProductionStage(models.Model):
    """Configurable list of production stages per line type."""

    _name = "fahm.production.stage"
    _description = "Water Production Stage"
    _order = "line_type, sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    line_type = fields.Selection(LINE_TYPES, required=True, default="bottle")
    unit_label = fields.Char(string="Counted In", default="bottles",
                             help="What operators count at this stage, e.g. bottles, jars, packs.")
    is_final = fields.Boolean(
        string="Final Stage",
        help="The good quantity of the final stage becomes the produced quantity of the order. "
             "Count it in the unit of the finished product (e.g. packs).")
    description = fields.Text(string="Instructions")


class FahmProductionStageLine(models.Model):
    """One stage of one manufacturing order, recorded by the operators."""

    _name = "fahm.production.stage.line"
    _description = "Production Stage Record"
    _order = "sequence, id"

    production_id = fields.Many2one("mrp.production", required=True, ondelete="cascade", index=True)
    stage_id = fields.Many2one("fahm.production.stage", required=True, ondelete="restrict")
    name = fields.Char(related="stage_id.name")
    sequence = fields.Integer(default=10)
    unit_label = fields.Char(related="stage_id.unit_label", string="Unit")
    is_final = fields.Boolean(related="stage_id.is_final")
    state = fields.Selection(
        [("waiting", "Waiting"), ("progress", "In Progress"), ("done", "Done")],
        default="waiting", required=True,
    )
    date_start = fields.Datetime(string="Started", readonly=True)
    date_end = fields.Datetime(string="Finished", readonly=True)
    duration = fields.Float(string="Duration (h)", compute="_compute_duration", store=True)
    operator_id = fields.Many2one("res.users", string="Operator")
    qty_good = fields.Float(string="Good Qty", digits="Product Unit")
    qty_rejected = fields.Float(string="Rejected Qty", digits="Product Unit")
    reject_reason_ids = fields.Many2many("stock.scrap.reason.tag", string="Reject Reasons")
    note = fields.Char()

    @api.depends("date_start", "date_end")
    def _compute_duration(self):
        for line in self:
            if line.date_start and line.date_end:
                line.duration = (line.date_end - line.date_start).total_seconds() / 3600.0
            else:
                line.duration = 0.0

    def _previous_lines(self):
        self.ensure_one()
        return self.production_id.fahm_stage_line_ids.filtered(
            lambda l: (l.sequence, l.id) < (self.sequence, self.id))

    def action_start(self):
        for line in self:
            production = line.production_id
            production._fahm_check_can_produce()
            if line.state != "waiting":
                continue
            unfinished = line._previous_lines().filtered(lambda l: l.state != "done")
            if unfinished:
                raise UserError(self.env._(
                    "Finish '%(prev)s' before starting '%(stage)s'.",
                    prev=unfinished[0].name, stage=line.name))
            line.write({
                "state": "progress",
                "date_start": fields.Datetime.now(),
                "operator_id": line.operator_id.id or self.env.user.id,
            })
            if production.state == "confirmed":
                production.action_start()
        return True

    def action_finish(self):
        for line in self:
            if line.state != "progress":
                raise UserError(self.env._("Start '%s' before finishing it.", line.name))
            if line.qty_good <= 0:
                raise UserError(self.env._("Enter the good quantity of '%s' before finishing it.", line.name))
            line.write({"state": "done", "date_end": fields.Datetime.now()})
            production = line.production_id
            if line.is_final:
                production.qty_producing = line.qty_good
                production.message_post(body=self.env._(
                    "Final stage '%(stage)s' finished: %(qty)s %(unit)s produced.",
                    stage=line.name, qty=line.qty_good, unit=line.unit_label or ""))
        return True

    def action_reopen(self):
        if not self.env.user.has_group("mrp.group_mrp_manager"):
            raise UserError(self.env._("Only a Manufacturing Administrator can reopen a stage."))
        for line in self:
            if line.production_id.state in ("done", "cancel"):
                raise UserError(self.env._("The order is closed."))
        self.write({"state": "progress", "date_end": False})
