from . import models


def post_init_hook(env):
    """Create the tank, production floor and water production operation for every
    warehouse, and attach existing finished-water BoMs to that operation."""
    env["stock.warehouse"].search([])._fahm_ensure_water_setup()
    env["mrp.bom"].search([])._fahm_set_water_operation()
