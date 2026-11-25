from odoo import api, fields, models
from odoo.exceptions import UserError

from .qc_parameter import QC_STAGES


class FahmWaterQcCheck(models.Model):
    _name = "fahm.water.qc.check"
    _description = "Water Quality Check"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False, default="New"
    )
    check_type = fields.Selection(
        QC_STAGES, string="Stage", required=True, default="finished_product", tracking=True
    )
    date = fields.Datetime(required=True, default=fields.Datetime.now, tracking=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    production_id = fields.Many2one(
        "mrp.production", string="Manufacturing Order", check_company=True, index=True
    )
    product_id = fields.Many2one("product.product", string="Product", check_company=True)
    lot_id = fields.Many2one(
        "stock.lot", string="Lot / Batch", check_company=True,
        domain="[('product_id', '=', product_id)]",
    )
    source_id = fields.Many2one("fahm.water.source", string="Water Source", check_company=True)
    sampling_point = fields.Char(
        help="Where the sample was taken, e.g. after RO, after ozonation, filler bowl."
    )
    inspector_id = fields.Many2one(
        "res.users", string="Inspector", default=lambda self: self.env.user, tracking=True
    )
    line_ids = fields.One2many("fahm.water.qc.check.line", "check_id", string="Results", copy=True)
    state = fields.Selection(
        [("draft", "To Evaluate"), ("passed", "Passed"), ("failed", "Failed")],
        default="draft", required=True, tracking=True, copy=False,
    )
    date_evaluated = fields.Datetime(readonly=True, copy=False)
    failed_count = fields.Integer(compute="_compute_counts")
    line_count = fields.Integer(compute="_compute_counts")
    note = fields.Html(string="Notes")

    @api.depends("line_ids.result")
    def _compute_counts(self):
        for check in self:
            check.line_count = len(check.line_ids)
            check.failed_count = len(check.line_ids.filtered(lambda l: l.result == "fail"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("fahm.water.qc.check") or "New"
            if vals.get("production_id"):
                production = self.env["mrp.production"].browse(vals["production_id"])
                vals.setdefault("product_id", production.product_id.id)
                vals.setdefault("company_id", production.company_id.id)
                if not vals.get("lot_id") and production.lot_producing_ids:
                    vals["lot_id"] = production.lot_producing_ids[:1].id
        checks = super().create(vals_list)
        checks.filtered(lambda c: not c.line_ids)._load_parameters()
        return checks

    def _load_parameters(self):
        Parameter = self.env["fahm.water.qc.parameter"]
        for check in self:
            params = Parameter.search([(f"stage_{check.check_type}", "=", True)])
            existing = check.line_ids.parameter_id
            check.line_ids = [
                fields.Command.create({"parameter_id": p.id, "sequence": p.sequence})
                for p in params - existing
            ]

    def action_load_parameters(self):
        self._check_draft()
        self._load_parameters()

    def _check_draft(self):
        if any(check.state != "draft" for check in self):
            raise UserError(self.env._("Only checks that are still to evaluate can be changed."))

    def action_evaluate(self):
        self._check_draft()
        for check in self:
            if not check.line_ids:
                raise UserError(self.env._("QC check %s has no test results.", check.name))
            pending = check.line_ids.filtered(lambda l: l.result == "pending")
            if pending:
                raise UserError(self.env._(
                    "Enter a result for every test before evaluating %(check)s. Missing: %(tests)s",
                    check=check.name,
                    tests=", ".join(pending.parameter_id.mapped("name")),
                ))
            failed = check.line_ids.filtered(lambda l: l.result == "fail")
            check.write({
                "state": "failed" if failed else "passed",
                "date_evaluated": fields.Datetime.now(),
            })
            if failed:
                check.message_post(body=self.env._(
                    "QC check failed on: %s", ", ".join(failed.parameter_id.mapped("name"))
                ))
        return True

    def action_reset_draft(self):
        if not self.env.user.has_group("fahm_water_manufacturing.group_water_qc_manager"):
            raise UserError(self.env._("Only a Water Quality Control manager can reopen a QC check."))
        self.write({"state": "draft", "date_evaluated": False})

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(check.state != "draft" for check in self):
            raise UserError(self.env._("Evaluated QC checks cannot be deleted. Reset them to draft first."))


class FahmWaterQcCheckLine(models.Model):
    _name = "fahm.water.qc.check.line"
    _description = "Water Quality Check Result"
    _order = "sequence, id"

    check_id = fields.Many2one("fahm.water.qc.check", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    parameter_id = fields.Many2one("fahm.water.qc.parameter", required=True, ondelete="restrict")
    category = fields.Selection(related="parameter_id.category")
    test_type = fields.Selection(related="parameter_id.test_type")
    unit = fields.Char(related="parameter_id.unit")
    limit_display = fields.Char(related="parameter_id.limit_display", string="Limits")
    value = fields.Float(string="Measured Value", digits=(16, 3))
    is_measured = fields.Boolean(
        string="Measured",
        help="Ticked automatically when a value is entered. Tick it for a measured value of 0.",
    )
    pass_fail_result = fields.Selection(
        [("pass", "Conforms"), ("fail", "Does Not Conform")], string="Observation"
    )
    result = fields.Selection(
        [("pending", "Pending"), ("pass", "Pass"), ("fail", "Fail")],
        compute="_compute_result", store=True,
    )
    note = fields.Char()

    @api.onchange("value")
    def _onchange_value(self):
        if self.value:
            self.is_measured = True

    @api.depends("value", "is_measured", "pass_fail_result", "parameter_id",
                 "parameter_id.test_type", "parameter_id.min_value", "parameter_id.max_value",
                 "parameter_id.has_min", "parameter_id.has_max")
    def _compute_result(self):
        for line in self:
            param = line.parameter_id
            if not param:
                line.result = "pending"
            elif param.test_type == "pass_fail":
                line.result = line.pass_fail_result or "pending"
            elif not (line.is_measured or line.value):
                line.result = "pending"
            else:
                line.result = "pass" if param._evaluate(line.value) else "fail"

    def write(self, vals):
        self.check_id._check_draft()
        if "value" in vals and vals["value"] and "is_measured" not in vals:
            vals = dict(vals, is_measured=True)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("value") and "is_measured" not in vals:
                vals["is_measured"] = True
        lines = super().create(vals_list)
        lines.check_id._check_draft()
        return lines

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        self.check_id._check_draft()
