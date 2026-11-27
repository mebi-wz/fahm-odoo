from odoo import api, fields, models
from odoo.exceptions import UserError


class FahmFgHandover(models.Model):
    """Production hands finished goods over to the store.

    Produce puts finished goods into the warehouse's *Finished Goods Staging* location.
    Production submits a handover, which creates a transfer staging -> main stock; the
    store keeper validates it when the goods are physically received.
    """

    _name = "fahm.fg.handover"
    _description = "Finished Goods Handover"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="Reference", required=True, readonly=True, copy=False, default="New")
    date = fields.Datetime(required=True, default=fields.Datetime.now)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    production_id = fields.Many2one(
        "mrp.production", string="Manufacturing Order", required=True, check_company=True,
        index=True, ondelete="restrict")
    warehouse_id = fields.Many2one("stock.warehouse", required=True, check_company=True)
    handed_over_by_id = fields.Many2one(
        "res.users", string="Handed Over By", default=lambda self: self.env.user, tracking=True)
    received_by_id = fields.Many2one("res.users", string="Received By", readonly=True, copy=False)
    date_received = fields.Datetime(readonly=True, copy=False)
    line_ids = fields.One2many("fahm.fg.handover.line", "handover_id", string="Finished Goods", copy=True)
    picking_ids = fields.One2many("stock.picking", "fahm_fg_handover_id", string="Transfers")
    picking_count = fields.Integer(compute="_compute_picking_count")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Waiting Store Receipt"),
            ("received", "Received"),
            ("cancel", "Cancelled"),
        ],
        default="draft", required=True, tracking=True, copy=False,
    )
    note = fields.Text()

    @api.depends("picking_ids")
    def _compute_picking_count(self):
        for handover in self:
            handover.picking_count = len(handover.picking_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("fahm.fg.handover") or "New"
        return super().create(vals_list)

    @api.model
    def _create_from_production(self, production):
        warehouse = production.picking_type_id.warehouse_id
        staging = warehouse.fahm_fg_location_id
        move_lines = production.move_finished_ids.filtered(
            lambda m: m.state == "done" and m.product_id == production.product_id
            and m.location_dest_id == staging).move_line_ids
        if not move_lines:
            raise UserError(self.env._(
                "%s has no finished goods in the Finished Goods Staging location.", production.name))
        lines = {}
        for ml in move_lines:
            key = (ml.product_id.id, ml.lot_id.id)
            qty = ml.product_uom_id._compute_quantity(ml.quantity, ml.product_id.uom_id)
            lines[key] = lines.get(key, 0.0) + qty
        return self.create({
            "production_id": production.id,
            "warehouse_id": warehouse.id,
            "company_id": production.company_id.id,
            "line_ids": [
                fields.Command.create({"product_id": product_id, "lot_id": lot_id, "quantity": qty})
                for (product_id, lot_id), qty in lines.items()
            ],
        })

    def action_submit(self):
        for handover in self:
            if handover.state != "draft":
                raise UserError(self.env._("%s has already been submitted.", handover.name))
            if not handover.line_ids.filtered(lambda l: l.quantity > 0):
                raise UserError(self.env._("Add the finished goods to hand over."))
            handover._create_picking()
            handover.state = "submitted"
            handover._notify_store()
        return True

    def _create_picking(self):
        self.ensure_one()
        warehouse = self.warehouse_id
        staging = warehouse.fahm_fg_location_id
        stock = warehouse.lot_stock_id
        picking = self.env["stock.picking"].create({
            "picking_type_id": warehouse.int_type_id.id,
            "location_id": staging.id,
            "location_dest_id": stock.id,
            "origin": f"{self.name} / {self.production_id.name}",
            "company_id": self.company_id.id,
            "fahm_fg_handover_id": self.id,
        })
        by_product = {}
        for line in self.line_ids.filtered(lambda l: l.quantity > 0):
            by_product.setdefault(line.product_id, self.env["fahm.fg.handover.line"])
            by_product[line.product_id] |= line
        for product, lines in by_product.items():
            move = self.env["stock.move"].create({
                "picking_id": picking.id,
                "product_id": product.id,
                "product_uom": product.uom_id.id,
                "product_uom_qty": sum(lines.mapped("quantity")),
                "location_id": staging.id,
                "location_dest_id": stock.id,
                "company_id": self.company_id.id,
            })
            move._action_confirm()
            # reserve exactly the lots produced by this order
            move.move_line_ids.unlink()
            for line in lines:
                self.env["stock.move.line"].create({
                    "move_id": move.id,
                    "picking_id": picking.id,
                    "product_id": product.id,
                    "product_uom_id": product.uom_id.id,
                    "location_id": staging.id,
                    "location_dest_id": stock.id,
                    "lot_id": line.lot_id.id,
                    "quantity": line.quantity,
                })
        return picking

    def _notify_store(self):
        group = self.env.ref("stock.group_stock_manager", raise_if_not_found=False)
        if not group:
            return
        for handover in self:
            for user in group.user_ids.filtered(lambda u: handover.company_id in u.company_ids)[:5]:
                handover.activity_schedule(
                    "mail.mail_activity_data_todo", user_id=user.id,
                    summary=self.env._("Receive finished goods %s", handover.name))

    def _update_receipt_state(self):
        for handover in self.filtered(lambda h: h.state == "submitted"):
            pickings = handover.picking_ids.filtered(lambda p: p.state != "cancel")
            if pickings and all(p.state == "done" for p in pickings):
                handover.write({
                    "state": "received",
                    "received_by_id": self.env.user.id,
                    "date_received": fields.Datetime.now(),
                })
                handover.activity_feedback(["mail.mail_activity_data_todo"])
                handover.production_id.message_post(body=self.env._(
                    "Finished goods received by the store (%s).", handover.name))

    def action_cancel(self):
        for handover in self:
            if handover.state == "received":
                raise UserError(self.env._("A received handover cannot be cancelled."))
            handover.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")).action_cancel()
        self.write({"state": "cancel"})
        self.activity_unlink(["mail.mail_activity_data_todo"])

    def action_view_pickings(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("stock.action_picking_tree_all")
        action["domain"] = [("id", "in", self.picking_ids.ids)]
        action["context"] = {"create": False}
        if len(self.picking_ids) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = self.picking_ids.id
        return action


class FahmFgHandoverLine(models.Model):
    _name = "fahm.fg.handover.line"
    _description = "Finished Goods Handover Line"

    handover_id = fields.Many2one("fahm.fg.handover", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot", string="Lot / Batch", domain="[('product_id', '=', product_id)]")
    expiration_date = fields.Datetime(related="lot_id.expiration_date", string="Best Before")
    quantity = fields.Float(digits="Product Unit", required=True)
    uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")

    def write(self, vals):
        if any(line.handover_id.state != "draft" for line in self):
            raise UserError(self.env._("Lines can only be changed while the handover is in draft."))
        return super().write(vals)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    fahm_fg_handover_id = fields.Many2one(
        "fahm.fg.handover", string="Finished Goods Handover", index=True, readonly=True, copy=True)

    def _action_done(self):
        res = super()._action_done()
        self.fahm_fg_handover_id._update_receipt_state()
        return res
