from odoo import api, fields, models
from odoo.exceptions import ValidationError

WATER_TYPES = [
    ("finished_water", "Finished Water"),
    ("treated_water", "Treated Water"),
    ("preform", "PET Preform"),
    ("bottle", "Empty Bottle"),
    ("cap", "Cap / Closure"),
    ("label", "Label"),
    ("shrink_film", "Shrink Film"),
    ("carton", "Carton / Tray"),
    ("jar", "20 L Jar"),
    ("chemical", "Treatment Chemical / Mineral"),
    ("other", "Other"),
]


class ProductTemplate(models.Model):
    _inherit = "product.template"

    fahm_water_type = fields.Selection(
        WATER_TYPES,
        string="Water Plant Type",
        help="Classifies the product for the water plant (finished water, packaging material, ...).",
    )
    fahm_volume_per_unit = fields.Float(
        string="Litres per Unit",
        digits=(16, 3),
        help="Litres of water contained in one unit of this product's unit of measure. "
             "For a pack of 24 × 0.5 L bottles, enter 12.",
    )
    fahm_units_per_pack = fields.Integer(
        string="Bottles per Pack",
        default=1,
        help="Number of bottles or jars contained in one unit of this product.",
    )
    fahm_qc_required = fields.Boolean(
        string="QC Release Required",
        help="Manufacturing orders for this product can only be marked as done "
             "after a finished-product QC check has passed.",
    )

    @api.constrains("fahm_volume_per_unit", "fahm_units_per_pack")
    def _check_fahm_quantities(self):
        for product in self:
            if product.fahm_volume_per_unit < 0:
                raise ValidationError(self.env._("Litres per unit cannot be negative."))
            if product.fahm_units_per_pack < 0:
                raise ValidationError(self.env._("Bottles per pack cannot be negative."))

    @api.onchange("fahm_water_type")
    def _onchange_fahm_water_type(self):
        if self.fahm_water_type == "finished_water":
            self.fahm_qc_required = True
