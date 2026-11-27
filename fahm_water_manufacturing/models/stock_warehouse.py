from odoo import api, fields, models


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    fahm_tank_location_id = fields.Many2one(
        "stock.location", string="Treated Water Tank", readonly=True, copy=False,
        help="Where treated water is stored after a treatment batch has passed QC.",
    )
    fahm_floor_location_id = fields.Many2one(
        "stock.location", string="Production Floor", readonly=True, copy=False,
        help="Materials issued by the store for water production are moved here. "
             "Water production orders consume their components from this location.",
    )
    fahm_fg_location_id = fields.Many2one(
        "stock.location", string="Finished Goods Staging", readonly=True, copy=False,
        help="Water production puts finished goods here. Production then hands them over "
             "to the store, which receives them into the main stock.",
    )
    fahm_production_type_id = fields.Many2one(
        "stock.picking.type", string="Water Production Operation", readonly=True, copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        warehouses = super().create(vals_list)
        warehouses._fahm_ensure_water_setup()
        return warehouses

    def _fahm_ensure_water_setup(self):
        """Create the tank, production floor and water production operation type if missing."""
        Location = self.env["stock.location"].sudo()
        PickingType = self.env["stock.picking.type"].sudo()
        for warehouse in self:
            vals = {}
            if not warehouse.fahm_tank_location_id:
                vals["fahm_tank_location_id"] = Location.create({
                    "name": "Treated Water Tank",
                    "usage": "internal",
                    "location_id": warehouse.view_location_id.id,
                    "company_id": warehouse.company_id.id,
                }).id
            if not warehouse.fahm_floor_location_id:
                vals["fahm_floor_location_id"] = Location.create({
                    "name": "Production Floor",
                    "usage": "internal",
                    "location_id": warehouse.view_location_id.id,
                    "company_id": warehouse.company_id.id,
                }).id
            if not warehouse.fahm_fg_location_id:
                vals["fahm_fg_location_id"] = Location.create({
                    "name": "Finished Goods Staging",
                    "usage": "internal",
                    "location_id": warehouse.view_location_id.id,
                    "company_id": warehouse.company_id.id,
                }).id
            floor_id = vals.get("fahm_floor_location_id") or warehouse.fahm_floor_location_id.id
            fg_id = vals.get("fahm_fg_location_id") or warehouse.fahm_fg_location_id.id
            if not warehouse.fahm_production_type_id:
                vals["fahm_production_type_id"] = PickingType.create({
                    "name": "Water Production",
                    "code": "mrp_operation",
                    "sequence_code": "WMO",
                    "warehouse_id": warehouse.id,
                    "company_id": warehouse.company_id.id,
                    "default_location_src_id": floor_id,
                    "default_location_dest_id": fg_id,
                    "use_create_components_lots": True,
                }).id
            elif warehouse.fahm_production_type_id.default_location_dest_id == warehouse.lot_stock_id:
                # databases set up before the staging location existed
                warehouse.fahm_production_type_id.sudo().default_location_dest_id = fg_id
            if vals:
                warehouse.sudo().write(vals)
        return True

    def _fahm_issue_source_location(self, product):
        """Location the store issues a product from: treated water comes from the tank."""
        self.ensure_one()
        if product.fahm_water_type == "treated_water":
            return self.fahm_tank_location_id
        return self.lot_stock_id
