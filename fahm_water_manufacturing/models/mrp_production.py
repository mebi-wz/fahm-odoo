from odoo import api, fields, models
from odoo.exceptions import UserError


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    fahm_line_id = fields.Many2one(
        "mrp.workcenter",
        string="Production Line",
        domain="[('fahm_is_water_line', '=', True), ('company_id', 'in', (company_id, False))]",
        tracking=True,
    )
    fahm_shift_id = fields.Many2one("fahm.production.shift", string="Shift", tracking=True)
    fahm_operator_ids = fields.Many2many("res.users", string="Operators")
    fahm_water_type = fields.Selection(related="product_id.fahm_water_type")
    fahm_qc_required = fields.Boolean(
        related="product_id.fahm_qc_required", string="QC Release Required"
    )
    fahm_qc_check_ids = fields.One2many("fahm.water.qc.check", "production_id", string="QC Checks")
    fahm_qc_check_count = fields.Integer(compute="_compute_fahm_qc_check_count")
    fahm_qc_state = fields.Selection(
        [
            ("not_required", "Not Required"),
            ("pending", "Pending"),
            ("passed", "Passed"),
            ("failed", "Failed"),
        ],
        string="QC Status",
        compute="_compute_fahm_qc_state",
        store=True,
    )
    fahm_qty_produced = fields.Float(
        string="Produced (Product UoM)",
        compute="_compute_fahm_production_figures",
        store=True,
        digits="Product Unit",
    )
    fahm_liters_produced = fields.Float(
        string="Litres Produced",
        compute="_compute_fahm_production_figures",
        store=True,
        digits=(16, 2),
    )
    fahm_reject_qty = fields.Float(
        string="Rejected Units",
        compute="_compute_fahm_reject_qty",
        digits="Product Unit",
        help="Quantity of the finished product scrapped on this order.",
    )

    @api.depends("fahm_qc_check_ids")
    def _compute_fahm_qc_check_count(self):
        for production in self:
            production.fahm_qc_check_count = len(production.fahm_qc_check_ids)

    @api.depends("fahm_qc_check_ids.state", "fahm_qc_check_ids.date",
                 "fahm_qc_check_ids.check_type", "product_id.fahm_qc_required")
    def _compute_fahm_qc_state(self):
        for production in self:
            checks = production.fahm_qc_check_ids.filtered(
                lambda c: c.check_type == "finished_product"
            )
            # The most recent evaluation decides the release.
            latest = checks.sorted(lambda c: (c.date, c.id), reverse=True)[:1]
            if latest.state in ("passed", "failed"):
                production.fahm_qc_state = latest.state
            elif production.product_id.fahm_qc_required or checks:
                production.fahm_qc_state = "pending"
            else:
                production.fahm_qc_state = "not_required"

    @api.depends("state", "qty_producing", "product_uom_id", "product_id.fahm_volume_per_unit")
    def _compute_fahm_production_figures(self):
        for production in self:
            if production.state != "done":
                production.fahm_qty_produced = 0.0
                production.fahm_liters_produced = 0.0
                continue
            qty = production.product_uom_id._compute_quantity(
                production.qty_producing, production.product_id.uom_id
            )
            production.fahm_qty_produced = qty
            production.fahm_liters_produced = qty * production.product_id.fahm_volume_per_unit

    @api.depends("scrap_ids.state", "scrap_ids.scrap_qty", "scrap_ids.product_id")
    def _compute_fahm_reject_qty(self):
        for production in self:
            scraps = production.scrap_ids.filtered(
                lambda s: s.state == "done" and s.product_id == production.product_id
            )
            production.fahm_reject_qty = sum(
                s.product_uom_id._compute_quantity(s.scrap_qty, s.product_id.uom_id) for s in scraps
            )

    def action_fahm_create_qc_check(self):
        self.ensure_one()
        if self.state in ("draft", "cancel"):
            raise UserError(self.env._("Confirm the manufacturing order before creating a QC check."))
        check = self.env["fahm.water.qc.check"].create({
            "check_type": "finished_product",
            "production_id": self.id,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "fahm.water.qc.check",
            "res_id": check.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_fahm_view_qc_checks(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "fahm_water_manufacturing.action_fahm_water_qc_check"
        )
        action["domain"] = [("production_id", "=", self.id)]
        action["context"] = {
            "default_production_id": self.id,
            "default_check_type": "finished_product",
        }
        if self.fahm_qc_check_count == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = self.fahm_qc_check_ids.id
        return action

    def pre_button_mark_done(self):
        blocked = self.filtered(lambda p: p.fahm_qc_required and p.fahm_qc_state != "passed")
        if blocked:
            raise UserError(self.env._(
                "These manufacturing orders need a passed finished-product QC check "
                "before they can be marked as done:\n%s",
                "\n".join(
                    f"- {p.name} (QC: {dict(p._fields['fahm_qc_state']._description_selection(self.env)).get(p.fahm_qc_state)})"
                    for p in blocked
                ),
            ))
        return super().pre_button_mark_done()
