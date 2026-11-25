from odoo import api, fields, models
from odoo.exceptions import ValidationError

QC_STAGES = [
    ("raw_water", "Raw Water"),
    ("treated_water", "Treated Water"),
    ("finished_product", "Finished Product"),
]


class FahmWaterQcParameter(models.Model):
    _name = "fahm.water.qc.parameter"
    _description = "Water Quality Parameter"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    category = fields.Selection(
        [
            ("physical", "Physical"),
            ("chemical", "Chemical"),
            ("microbiological", "Microbiological"),
            ("packaging", "Packaging / Visual"),
        ],
        required=True,
        default="physical",
    )
    stage_raw_water = fields.Boolean(string="Raw Water")
    stage_treated_water = fields.Boolean(string="Treated Water")
    stage_finished_product = fields.Boolean(string="Finished Product")
    test_type = fields.Selection(
        [("measure", "Measurement"), ("pass_fail", "Pass / Fail")],
        required=True,
        default="measure",
    )
    unit = fields.Char(help="Unit of the measured value, e.g. mg/L, NTU, µS/cm, CFU/100 mL.")
    min_value = fields.Float(string="Minimum", digits=(16, 3))
    max_value = fields.Float(string="Maximum", digits=(16, 3))
    has_min = fields.Boolean(string="Has Minimum")
    has_max = fields.Boolean(string="Has Maximum")
    method = fields.Char(string="Test Method")
    description = fields.Text()

    @api.constrains("test_type", "has_min", "has_max", "min_value", "max_value")
    def _check_limits(self):
        for param in self:
            if param.test_type != "measure":
                continue
            if not (param.has_min or param.has_max):
                raise ValidationError(
                    self.env._("Measurement parameter '%s' needs a minimum and/or maximum limit.", param.name)
                )
            if param.has_min and param.has_max and param.min_value > param.max_value:
                raise ValidationError(
                    self.env._("The minimum of '%s' is greater than its maximum.", param.name)
                )

    @api.depends("test_type", "has_min", "has_max", "min_value", "max_value", "unit")
    def _compute_limit_display(self):
        for param in self:
            param.limit_display = param._format_limits()

    limit_display = fields.Char(string="Limits", compute="_compute_limit_display")

    def _format_limits(self):
        self.ensure_one()
        if self.test_type == "pass_fail":
            return self.env._("Conforms")
        unit = f" {self.unit}" if self.unit else ""
        if self.has_min and self.has_max:
            return f"{self.min_value:g} – {self.max_value:g}{unit}"
        if self.has_max:
            return f"≤ {self.max_value:g}{unit}"
        if self.has_min:
            return f"≥ {self.min_value:g}{unit}"
        return ""

    def _evaluate(self, value):
        """Return True if a measured value is within the parameter limits."""
        self.ensure_one()
        if self.has_min and value < self.min_value:
            return False
        if self.has_max and value > self.max_value:
            return False
        return True

    def _applies_to(self, stage):
        self.ensure_one()
        return bool(self[f"stage_{stage}"])
