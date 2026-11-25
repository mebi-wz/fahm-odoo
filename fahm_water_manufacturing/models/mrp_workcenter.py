from odoo import fields, models


class MrpWorkcenter(models.Model):
    _inherit = "mrp.workcenter"

    fahm_is_water_line = fields.Boolean(string="Water Production Line")
    fahm_line_type = fields.Selection(
        [
            ("bottle", "Small Bottle Line (PET)"),
            ("jar", "20 L Jar Line"),
            ("blow_moulding", "Blow Moulding"),
            ("treatment", "Water Treatment"),
            ("packing", "Packing / Shrink Wrapping"),
        ],
        string="Line Type",
    )
    fahm_rated_capacity = fields.Float(
        string="Rated Capacity (units/hour)",
        help="Nominal output of the line, used to compute line efficiency.",
    )
