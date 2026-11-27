from odoo import api, fields, models


class LocalPurchaseRequisition(models.Model):
    _inherit = "local.purchase.requisition"

    fahm_material_request_id = fields.Many2one(
        "fahm.material.request", string="Production Material Request", readonly=True, copy=False,
        index=True, groups="mrp.group_mrp_user,stock.group_stock_user")
    fahm_production_id = fields.Many2one(
        related="fahm_material_request_id.production_id", string="Manufacturing Order",
        groups="mrp.group_mrp_user")
    # plain references readable by procurement staff without manufacturing access
    fahm_source_reference = fields.Char(
        string="Production Source", compute="_compute_fahm_source_reference", compute_sudo=True)

    @api.depends("fahm_material_request_id")
    def _compute_fahm_source_reference(self):
        for requisition in self:
            request = requisition.fahm_material_request_id
            requisition.fahm_source_reference = (
                f"{request.name} / {request.production_id.name}" if request else False)
