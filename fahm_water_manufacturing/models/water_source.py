from odoo import fields, models


class FahmWaterSource(models.Model):
    _name = "fahm.water.source"
    _description = "Water Source"
    _inherit = ["mail.thread"]
    _order = "sequence, name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    source_type = fields.Selection(
        [
            ("borehole", "Borehole / Well"),
            ("spring", "Spring"),
            ("municipal", "Municipal Supply"),
            ("other", "Other"),
        ],
        required=True,
        default="borehole",
        tracking=True,
    )
    location = fields.Char()
    depth_m = fields.Float(string="Depth (m)")
    licence_number = fields.Char(string="Abstraction Licence No.", tracking=True)
    licence_expiry = fields.Date(string="Licence Expiry", tracking=True)
    note = fields.Html()
    qc_check_ids = fields.One2many("fahm.water.qc.check", "source_id", string="QC Checks")
    qc_check_count = fields.Integer(compute="_compute_qc_check_count")

    _code_company_uniq = models.Constraint(
        "unique(code, company_id)",
        "The water source code must be unique per company.",
    )

    def _compute_qc_check_count(self):
        counts = dict(
            self.env["fahm.water.qc.check"]._read_group(
                [("source_id", "in", self.ids)], ["source_id"], ["__count"]
            )
        )
        for source in self:
            source.qc_check_count = counts.get(source, 0)

    def action_view_qc_checks(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "fahm_water_manufacturing.action_fahm_water_qc_check"
        )
        action["domain"] = [("source_id", "=", self.id)]
        action["context"] = {"default_source_id": self.id, "default_check_type": "raw_water"}
        return action
