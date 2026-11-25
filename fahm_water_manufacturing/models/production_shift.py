from odoo import api, fields, models
from odoo.exceptions import ValidationError


class FahmProductionShift(models.Model):
    _name = "fahm.production.shift"
    _description = "Production Shift"
    _order = "sequence, hour_from"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    hour_from = fields.Float(string="Starts At", required=True, default=6.0)
    hour_to = fields.Float(string="Ends At", required=True, default=14.0)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)

    @api.constrains("hour_from", "hour_to")
    def _check_hours(self):
        for shift in self:
            for hour in (shift.hour_from, shift.hour_to):
                if not 0 <= hour <= 24:
                    raise ValidationError(self.env._("Shift hours must be between 00:00 and 24:00."))
            if shift.hour_from == shift.hour_to:
                raise ValidationError(self.env._("A shift cannot start and end at the same time."))
