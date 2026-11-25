from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class FahmWaterTreatment(models.Model):
    """One treatment run: raw water drawn from a source (meter reading) is treated
    and, once QC has passed, the treated water is put into the treated water tank."""

    _name = "fahm.water.treatment"
    _description = "Water Treatment Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="Reference", required=True, readonly=True, copy=False, default="New")
    date = fields.Datetime(required=True, default=fields.Datetime.now, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one(
        "stock.warehouse", required=True, check_company=True,
        default=lambda self: self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1),
    )
    source_id = fields.Many2one("fahm.water.source", string="Water Source", required=True,
                                check_company=True, tracking=True)
    product_id = fields.Many2one(
        "product.product", string="Treated Water Product", required=True,
        domain="[('fahm_water_type', '=', 'treated_water')]",
        default=lambda self: self.env["product.product"].search(
            [("fahm_water_type", "=", "treated_water")], limit=1),
    )
    operator_id = fields.Many2one("res.users", string="Operator", default=lambda self: self.env.user)
    meter_start = fields.Float(string="Raw Water Meter – Start", digits=(16, 2))
    meter_end = fields.Float(string="Raw Water Meter – End", digits=(16, 2))
    raw_water_qty = fields.Float(
        string="Raw Water Drawn (L)", compute="_compute_quantities", store=True, digits=(16, 2))
    treated_qty = fields.Float(string="Treated Water Produced (L)", digits=(16, 2), tracking=True)
    reject_qty = fields.Float(
        string="Reject / Waste Water (L)", compute="_compute_quantities", store=True, digits=(16, 2))
    recovery_rate = fields.Float(
        string="Recovery (%)", compute="_compute_quantities", store=True, digits=(16, 1))
    treatment_steps = fields.Char(
        default="Sand / carbon filtration → softening → RO → mineral dosing → ozone / UV",
        help="Treatment steps performed in this run.",
    )
    qc_check_ids = fields.One2many("fahm.water.qc.check", "treatment_id", string="QC Checks")
    qc_state = fields.Selection(
        [("pending", "Pending"), ("passed", "Passed"), ("failed", "Failed")],
        string="QC Status", compute="_compute_qc_state", store=True,
    )
    move_id = fields.Many2one("stock.move", string="Stock Move", readonly=True, copy=False)
    state = fields.Selection(
        [("draft", "Draft"), ("qc", "Waiting QC"), ("done", "In Tank"), ("cancel", "Cancelled")],
        default="draft", required=True, tracking=True, copy=False,
    )
    note = fields.Html()

    @api.depends("meter_start", "meter_end", "treated_qty")
    def _compute_quantities(self):
        for batch in self:
            raw = max(batch.meter_end - batch.meter_start, 0.0)
            batch.raw_water_qty = raw
            batch.reject_qty = max(raw - batch.treated_qty, 0.0) if raw else 0.0
            batch.recovery_rate = (100.0 * batch.treated_qty / raw) if raw else 0.0

    @api.depends("qc_check_ids.state", "qc_check_ids.date")
    def _compute_qc_state(self):
        for batch in self:
            latest = batch.qc_check_ids.sorted(lambda c: (c.date, c.id), reverse=True)[:1]
            batch.qc_state = latest.state if latest.state in ("passed", "failed") else "pending"

    @api.constrains("meter_start", "meter_end", "treated_qty")
    def _check_quantities(self):
        for batch in self:
            if batch.meter_end and batch.meter_end < batch.meter_start:
                raise ValidationError(self.env._("The end meter reading cannot be lower than the start reading."))
            if batch.treated_qty < 0:
                raise ValidationError(self.env._("Treated water quantity cannot be negative."))
            if batch.raw_water_qty and batch.treated_qty > batch.raw_water_qty:
                raise ValidationError(self.env._("Treated water cannot exceed the raw water drawn."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("fahm.water.treatment") or "New"
        return super().create(vals_list)

    def action_submit_qc(self):
        for batch in self:
            if batch.state != "draft":
                continue
            if batch.treated_qty <= 0:
                raise UserError(self.env._("Enter the treated water quantity of %s first.", batch.name))
            batch.state = "qc"
            if not batch.qc_check_ids.filtered(lambda c: c.state == "draft"):
                batch._create_qc_check()
        return True

    def _create_qc_check(self):
        self.ensure_one()
        return self.env["fahm.water.qc.check"].create({
            "check_type": "treated_water",
            "treatment_id": self.id,
            "source_id": self.source_id.id,
            "company_id": self.company_id.id,
            "sampling_point": self.env._("Treated water outlet"),
        })

    def action_open_qc(self):
        self.ensure_one()
        check = self.qc_check_ids.sorted(lambda c: (c.date, c.id), reverse=True)[:1]
        if not check or check.state == "failed":
            check = self._create_qc_check()
        return {
            "type": "ir.actions.act_window",
            "res_model": "fahm.water.qc.check",
            "res_id": check.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_done(self):
        for batch in self:
            if batch.state != "qc":
                raise UserError(self.env._("Send %s to QC first.", batch.name))
            if batch.qc_state != "passed":
                raise UserError(self.env._(
                    "Treated water of %s can only go to the tank after its QC check has passed.", batch.name))
            batch.warehouse_id._fahm_ensure_water_setup()
            batch.move_id = batch._create_tank_move()
            batch.state = "done"
        return True

    def _create_tank_move(self):
        self.ensure_one()
        product = self.product_id
        # Quantities are entered in litres; convert to the product's unit (e.g. m³).
        litre = self.env.ref("uom.product_uom_litre")
        qty = litre._compute_quantity(self.treated_qty, product.uom_id)
        move = self.env["stock.move"].create({
            "product_id": product.id,
            "product_uom": product.uom_id.id,
            "product_uom_qty": qty,
            "location_id": product.with_company(self.company_id).property_stock_production.id,
            "location_dest_id": self.warehouse_id.fahm_tank_location_id.id,
            "company_id": self.company_id.id,
            "origin": self.name,
        })
        move._action_confirm()
        move._action_assign()
        move.quantity = qty
        move.picked = True
        move._action_done()
        return move

    def action_cancel(self):
        if any(batch.state == "done" for batch in self):
            raise UserError(self.env._("A batch already in the tank cannot be cancelled."))
        self.state = "cancel"

    def action_draft(self):
        self.filtered(lambda b: b.state == "cancel").state = "draft"
