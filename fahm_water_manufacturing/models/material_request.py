from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class FahmMaterialRequest(models.Model):
    """Production asks the store for the materials of a manufacturing order.

    Flow: Draft -> Submitted (production) -> Approved (production manager)
    -> Issuing (store creates the transfer) -> Issued (transfer validated).
    """

    _name = "fahm.material.request"
    _description = "Production Material Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="Reference", required=True, readonly=True, copy=False, default="New")
    date = fields.Datetime(string="Requested On", required=True, default=fields.Datetime.now)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    production_id = fields.Many2one(
        "mrp.production", string="Manufacturing Order", required=True, check_company=True,
        index=True, ondelete="cascade",
    )
    product_id = fields.Many2one(related="production_id.product_id", string="Product to Produce")
    warehouse_id = fields.Many2one(
        "stock.warehouse", compute="_compute_warehouse_id", store=True, check_company=True)
    requested_by_id = fields.Many2one(
        "res.users", string="Requested By", default=lambda self: self.env.user, tracking=True)
    approved_by_id = fields.Many2one("res.users", string="Approved By", readonly=True, copy=False)
    date_approved = fields.Datetime(readonly=True, copy=False)
    issued_by_id = fields.Many2one("res.users", string="Issued By", readonly=True, copy=False)
    date_issued = fields.Datetime(readonly=True, copy=False)
    line_ids = fields.One2many("fahm.material.request.line", "request_id", string="Materials", copy=True)
    picking_ids = fields.One2many("stock.picking", "fahm_material_request_id", string="Transfers")
    picking_count = fields.Integer(compute="_compute_picking_count")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Waiting Approval"),
            ("approved", "Approved"),
            ("issuing", "Being Issued"),
            ("issued", "Issued"),
            ("rejected", "Rejected"),
            ("cancel", "Cancelled"),
        ],
        default="draft", required=True, tracking=True, copy=False,
    )
    reject_reason = fields.Text(readonly=True, copy=False)
    note = fields.Text()

    @api.depends("production_id.picking_type_id")
    def _compute_warehouse_id(self):
        for request in self:
            request.warehouse_id = request.production_id.picking_type_id.warehouse_id

    @api.depends("picking_ids")
    def _compute_picking_count(self):
        for request in self:
            request.picking_count = len(request.picking_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("fahm.material.request") or "New"
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def _check_state(self, allowed):
        for request in self:
            if request.state not in allowed:
                raise UserError(self.env._(
                    "%(name)s cannot do this while it is '%(state)s'.",
                    name=request.name,
                    state=dict(self._fields["state"]._description_selection(self.env))[request.state],
                ))

    def action_submit(self):
        self._check_state(["draft"])
        for request in self:
            lines = request.line_ids.filtered(lambda l: l.qty_requested > 0)
            if not lines:
                raise UserError(self.env._("Add at least one material with a quantity to %s.", request.name))
        self.write({"state": "submitted", "date": fields.Datetime.now()})
        self._notify_approvers()
        return True

    def _notify_approvers(self):
        group = self.env.ref("mrp.group_mrp_manager")
        for request in self:
            for user in group.user_ids.filtered(lambda u: request.company_id in u.company_ids)[:5]:
                request.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=user.id,
                    summary=self.env._("Approve material request %s", request.name),
                )

    def action_approve(self):
        if not self.env.user.has_group("mrp.group_mrp_manager"):
            raise UserError(self.env._("Only a Manufacturing Administrator can approve material requests."))
        self._check_state(["submitted"])
        self.write({
            "state": "approved",
            "approved_by_id": self.env.user.id,
            "date_approved": fields.Datetime.now(),
        })
        self.activity_feedback(["mail.mail_activity_data_todo"])
        return True

    def action_reject(self):
        if not self.env.user.has_group("mrp.group_mrp_manager"):
            raise UserError(self.env._("Only a Manufacturing Administrator can reject material requests."))
        self._check_state(["submitted", "approved"])
        reason = self.env.context.get("reject_reason")
        self.write({"state": "rejected", "reject_reason": reason or False})
        self.activity_unlink(["mail.mail_activity_data_todo"])
        return True

    def action_issue(self):
        """Store: create the internal transfer(s) to the production floor."""
        if not self.env.user.has_group("stock.group_stock_user"):
            raise UserError(self.env._("Only inventory users (store keepers) can issue materials."))
        self._check_state(["approved"])
        for request in self:
            request._create_issue_pickings()
            request.write({"state": "issuing", "issued_by_id": self.env.user.id})
        if len(self) == 1 and len(self.picking_ids) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "stock.picking",
                "res_id": self.picking_ids.id,
                "view_mode": "form",
                "target": "current",
            }
        return True

    def _create_issue_pickings(self):
        self.ensure_one()
        warehouse = self.warehouse_id
        if not warehouse:
            raise UserError(self.env._("The manufacturing order of %s has no warehouse.", self.name))
        warehouse._fahm_ensure_water_setup()
        floor = warehouse.fahm_floor_location_id
        picking_type = warehouse.int_type_id
        by_source = defaultdict(lambda: self.env["fahm.material.request.line"])
        for line in self.line_ids.filtered(lambda l: l.qty_requested > 0):
            by_source[warehouse._fahm_issue_source_location(line.product_id)] |= line
        pickings = self.env["stock.picking"]
        for source, lines in by_source.items():
            picking = self.env["stock.picking"].create({
                "picking_type_id": picking_type.id,
                "location_id": source.id,
                "location_dest_id": floor.id,
                "origin": f"{self.name} / {self.production_id.name}",
                "company_id": self.company_id.id,
                "fahm_material_request_id": self.id,
                "move_ids": [
                    fields.Command.create({
                        "product_id": line.product_id.id,
                        "product_uom": line.uom_id.id,
                        "product_uom_qty": line.qty_requested,
                        "location_id": source.id,
                        "location_dest_id": floor.id,
                        "company_id": self.company_id.id,
                    })
                    for line in lines
                ],
            })
            pickings |= picking
        pickings.action_confirm()
        pickings.action_assign()
        return pickings

    def _update_issue_state(self):
        """Called when a transfer is validated: mark the request issued when all transfers are done."""
        for request in self.filtered(lambda r: r.state == "issuing"):
            pickings = request.picking_ids.filtered(lambda p: p.state != "cancel")
            if pickings and all(p.state == "done" for p in pickings):
                request.write({"state": "issued", "date_issued": fields.Datetime.now()})
                production = request.production_id
                if production.state in ("confirmed", "progress"):
                    production.action_assign()
                    production._fahm_load_stages()
                production.message_post(body=self.env._(
                    "Materials issued by the store (%s). Production can start.", request.name))

    def action_cancel(self):
        self._check_state(["draft", "submitted", "approved", "rejected"])
        self.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")).action_cancel()
        self.write({"state": "cancel"})
        self.activity_unlink(["mail.mail_activity_data_todo"])

    def action_draft(self):
        self._check_state(["rejected", "cancel"])
        self.write({"state": "draft", "reject_reason": False})

    def action_view_pickings(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("stock.action_picking_tree_all")
        action["domain"] = [("id", "in", self.picking_ids.ids)]
        action["context"] = {"create": False}
        if len(self.picking_ids) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = self.picking_ids.id
        return action

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(r.state not in ("draft", "cancel") for r in self):
            raise UserError(self.env._("Only draft or cancelled material requests can be deleted."))


class FahmMaterialRequestLine(models.Model):
    _name = "fahm.material.request.line"
    _description = "Production Material Request Line"
    _order = "sequence, id"

    request_id = fields.Many2one("fahm.material.request", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one("product.product", required=True)
    fahm_water_type = fields.Selection(related="product_id.fahm_water_type")
    uom_id = fields.Many2one("uom.uom", string="Unit", required=True)
    qty_required = fields.Float(
        string="Needed (BoM)", digits="Product Unit", readonly=True,
        help="Quantity needed by the manufacturing order according to its bill of materials.")
    qty_requested = fields.Float(string="Requested", digits="Product Unit")
    qty_available = fields.Float(
        string="In Store", digits="Product Unit", compute="_compute_qty_available",
        help="Quantity on hand where the store issues this material from.")
    qty_issued = fields.Float(string="Issued", digits="Product Unit", compute="_compute_qty_issued")
    is_short = fields.Boolean(compute="_compute_qty_available")

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id and not self.uom_id:
            self.uom_id = self.product_id.uom_id

    @api.depends("product_id", "uom_id", "qty_requested", "request_id.warehouse_id")
    def _compute_qty_available(self):
        for line in self:
            warehouse = line.request_id.warehouse_id
            if not (line.product_id and warehouse):
                line.qty_available = 0.0
                line.is_short = False
                continue
            source = warehouse._fahm_issue_source_location(line.product_id)
            qty = line.product_id.with_context(location=source.id).qty_available if source else 0.0
            if line.uom_id and line.uom_id != line.product_id.uom_id:
                qty = line.product_id.uom_id._compute_quantity(qty, line.uom_id)
            line.qty_available = qty
            line.is_short = float_compare(qty, line.qty_requested, precision_digits=4) < 0

    @api.depends("request_id.picking_ids.state", "request_id.picking_ids.move_ids.quantity")
    def _compute_qty_issued(self):
        for line in self:
            moves = line.request_id.picking_ids.move_ids.filtered(
                lambda m: m.state == "done" and m.product_id == line.product_id)
            line.qty_issued = sum(
                m.product_uom._compute_quantity(m.quantity, line.uom_id or m.product_uom) for m in moves)

    def write(self, vals):
        if any(line.request_id.state not in ("draft",) for line in self) and \
                set(vals) - {"sequence"}:
            raise UserError(self.env._("Materials can only be changed while the request is in draft."))
        return super().write(vals)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    fahm_material_request_id = fields.Many2one(
        "fahm.material.request", string="Material Request", index=True, readonly=True,
        # copied so that backorders of a partial issue stay linked to the request
        copy=True,
    )

    def _action_done(self):
        res = super()._action_done()
        self.fahm_material_request_id._update_issue_state()
        return res
