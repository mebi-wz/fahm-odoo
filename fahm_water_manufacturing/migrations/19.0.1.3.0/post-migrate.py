from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Add the Finished Goods Staging location, point the Water Production operation
    to it, and send open water orders' finished goods there too."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    warehouses = env["stock.warehouse"].search([])
    warehouses._fahm_ensure_water_setup()
    for warehouse in warehouses:
        staging = warehouse.fahm_fg_location_id
        orders = env["mrp.production"].search([
            ("picking_type_id", "=", warehouse.fahm_production_type_id.id),
            ("state", "in", ("draft", "confirmed", "progress", "to_close")),
        ])
        for order in orders:
            order.location_dest_id = staging
            order.move_finished_ids.filtered(
                lambda m: m.state not in ("done", "cancel")).location_dest_id = staging
