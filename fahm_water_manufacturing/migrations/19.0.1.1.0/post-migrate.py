from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Existing databases: create the water warehouse setup and attach finished-water BoMs."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["stock.warehouse"].search([])._fahm_ensure_water_setup()
    env["mrp.bom"].search([])._fahm_set_water_operation()
