from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


# Groups allowed to read local purchase requisitions (see procurement_local_purchase access rules).
PURCHASE_GROUPS = ",".join([
    "procurement_store_requisition.group_store_requisition_user",
    "procurement_store_requisition.group_store_requisition_verifier",
    "procurement_store_requisition.group_store_requisition_budget_manager",
    "procurement_local_purchase.group_local_purchase_dept_manager",
    "procurement_local_purchase.group_local_purchase_pr_manager",
    "procurement_local_purchase.group_local_purchase_ceo",
])


class FahmMaterialRequest(models.Model):
    _inherit = "fahm.material.request"

    purchase_request_ids = fields.One2many(
        "local.purchase.requisition", "fahm_material_request_id", string="Purchase Requests",
        groups=PURCHASE_GROUPS)
    # computed as superuser so the count is visible to production users without purchase access
    purchase_request_count = fields.Integer(compute="_compute_purchase_request_count")
    can_purchase = fields.Boolean(compute="_compute_can_purchase")
    has_shortage = fields.Boolean(
        compute="_compute_has_shortage",
        help="At least one material is requested in a larger quantity than the store has.")

    def _compute_purchase_request_count(self):
        counts = dict(self.env["local.purchase.requisition"].sudo()._read_group(
            [("fahm_material_request_id", "in", self.ids)], ["fahm_material_request_id"], ["__count"]))
        for request in self:
            request.purchase_request_count = counts.get(request, 0)

    def _compute_can_purchase(self):
        allowed = self.env.su or any(self.env.user.has_group(g) for g in PURCHASE_GROUPS.split(","))
        for request in self:
            request.can_purchase = allowed

    @api.depends("line_ids.is_short", "state")
    def _compute_has_shortage(self):
        for request in self:
            request.has_shortage = request.state in ("draft", "submitted", "approved", "issuing") and any(
                line.is_short for line in request.line_ids)

    def _shortage_lines(self):
        """Lines that must be bought: short in store, excluding treated water
        (water is produced by a treatment batch, not purchased)."""
        self.ensure_one()
        return self.line_ids.filtered(
            lambda l: l.is_short and l.product_id.fahm_water_type != "treated_water")

    def action_create_purchase_request(self):
        self.ensure_one()
        if not self.can_purchase:
            raise UserError(self.env._(
                "You need the 'Store Requisition: Requester' role to create purchase requests."))
        if self.state not in ("draft", "submitted", "approved", "issuing"):
            raise UserError(self.env._("A purchase request can only be created for an open material request."))
        lines = self._shortage_lines()
        if not lines:
            if self.line_ids.filtered(lambda l: l.is_short):
                raise UserError(self.env._(
                    "Only treated water is short. Record a water treatment batch to fill the tank "
                    "instead of purchasing it."))
            raise UserError(self.env._("All requested materials are available in store."))

        open_pr = self.purchase_request_ids.filtered(lambda p: p.state == "draft")[:1]
        line_vals = []
        for line in lines:
            missing = line.qty_requested - max(line.qty_available, 0.0)
            # quantities on the purchase request are in the product's unit
            qty = line.uom_id._compute_quantity(missing, line.product_id.uom_id) \
                if line.uom_id != line.product_id.uom_id else missing
            if float_compare(qty, 0.0, precision_digits=4) <= 0:
                continue
            product = line.product_id
            line_vals.append({
                "product_id": product.id,
                "description": product.display_name,
                "quantity": qty,
                "product_uom_id": product.uom_id.id,
                "unit_price": product.standard_price,
            })
        if not line_vals:
            raise UserError(self.env._("Nothing is missing in store."))

        if open_pr:
            # refresh the existing draft request instead of creating a duplicate
            open_pr.line_ids.unlink()
            open_pr.write({"line_ids": [fields.Command.create(vals) for vals in line_vals]})
            purchase_request = open_pr
        else:
            employee = self.requested_by_id.employee_id
            purchase_request = self.env["local.purchase.requisition"].create({
                "requested_by": self.requested_by_id.id,
                "department_id": employee.department_id.id if employee else False,
                "company_id": self.company_id.id,
                "fahm_material_request_id": self.id,
                "purpose": self.env._(
                    "Materials not available in store for production %(mo)s (material request %(req)s).",
                    mo=self.production_id.name, req=self.name),
                "line_ids": [fields.Command.create(vals) for vals in line_vals],
            })
        self.message_post(body=self.env._(
            "Purchase request %s created for the materials missing in store.", purchase_request.name))
        return {
            "type": "ir.actions.act_window",
            "res_model": "local.purchase.requisition",
            "res_id": purchase_request.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_purchase_requests(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Purchase Requests"),
            "res_model": "local.purchase.requisition",
            "view_mode": "list,form",
            "domain": [("id", "in", self.purchase_request_ids.ids)],
            "context": {"create": False},
        }
