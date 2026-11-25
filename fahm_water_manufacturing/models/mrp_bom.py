from odoo import api, models


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    @api.model_create_multi
    def create(self, vals_list):
        boms = super().create(vals_list)
        boms._fahm_set_water_operation()
        return boms

    def write(self, vals):
        res = super().write(vals)
        if "product_tmpl_id" in vals or "picking_type_id" in vals:
            self._fahm_set_water_operation()
        return res

    def _fahm_set_water_operation(self):
        """Water BoMs use the 'Water Production' operation so that orders consume
        their components from the production floor (where the store issues them)."""
        for bom in self:
            if bom.picking_type_id or bom.product_tmpl_id.fahm_water_type != "finished_water":
                continue
            company = bom.company_id or self.env.company
            warehouse = self.env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
            if not warehouse:
                continue
            warehouse._fahm_ensure_water_setup()
            bom.picking_type_id = warehouse.fahm_production_type_id
