from odoo import api, fields, models
from odoo.exceptions import UserError


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    fahm_line_id = fields.Many2one(
        "mrp.workcenter",
        string="Production Line",
        domain="[('fahm_is_water_line', '=', True), ('company_id', 'in', (company_id, False))]",
        tracking=True,
    )
    fahm_shift_id = fields.Many2one("fahm.production.shift", string="Shift", tracking=True)
    fahm_operator_ids = fields.Many2many("res.users", string="Operators")
    fahm_water_type = fields.Selection(related="product_id.fahm_water_type")
    fahm_qc_required = fields.Boolean(
        related="product_id.fahm_qc_required", string="QC Release Required"
    )
    fahm_qc_check_ids = fields.One2many("fahm.water.qc.check", "production_id", string="QC Checks")
    fahm_qc_check_count = fields.Integer(compute="_compute_fahm_qc_check_count")
    fahm_qc_state = fields.Selection(
        [
            ("not_required", "Not Required"),
            ("pending", "Pending"),
            ("passed", "Passed"),
            ("failed", "Failed"),
        ],
        string="QC Status",
        compute="_compute_fahm_qc_state",
        store=True,
    )
    fahm_qty_produced = fields.Float(
        string="Produced (Product UoM)",
        compute="_compute_fahm_production_figures",
        store=True,
        digits="Product Unit",
    )
    fahm_liters_produced = fields.Float(
        string="Litres Produced",
        compute="_compute_fahm_production_figures",
        store=True,
        digits=(16, 2),
    )
    fahm_material_request_ids = fields.One2many(
        "fahm.material.request", "production_id", string="Material Requests")
    fahm_material_request_count = fields.Integer(compute="_compute_fahm_material_request_count")
    fahm_material_state = fields.Selection(
        [
            ("none", "Not Requested"),
            ("draft", "Draft"),
            ("submitted", "Waiting Approval"),
            ("approved", "Approved"),
            ("issuing", "Being Issued"),
            ("issued", "Issued"),
        ],
        string="Materials", compute="_compute_fahm_material_state", store=True,
    )
    fahm_materials_short = fields.Boolean(
        string="Materials Missing on Floor", compute="_compute_fahm_materials_short",
        help="The order needs more components than were issued (e.g. more was produced than planned).")
    fahm_handover_ids = fields.One2many(
        "fahm.fg.handover", "production_id", string="Finished Goods Handovers")
    fahm_handover_count = fields.Integer(compute="_compute_fahm_handover_count")
    fahm_handover_state = fields.Selection(
        [
            ("none_needed", "Not Needed"),
            ("to_hand_over", "To Hand Over"),
            ("submitted", "Waiting Store Receipt"),
            ("received", "Received by Store"),
        ],
        string="Handover", compute="_compute_fahm_handover_state", store=True,
    )
    fahm_stage_line_ids = fields.One2many(
        "fahm.production.stage.line", "production_id", string="Production Stages")
    fahm_step = fields.Selection(
        [
            ("plan", "1. Plan"),
            ("materials", "2. Request Materials"),
            ("approval", "3. Approval"),
            ("issue", "4. Store Issue"),
            ("production", "5. Production"),
            ("qc", "6. Quality Check"),
            ("close", "7. Close"),
            ("handover", "8. Hand Over to Store"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        string="Step", compute="_compute_fahm_step",
    )
    fahm_reject_qty = fields.Float(
        string="Rejected Units",
        compute="_compute_fahm_reject_qty",
        digits="Product Unit",
        help="Quantity of the finished product scrapped on this order.",
    )

    @api.depends("fahm_qc_check_ids")
    def _compute_fahm_qc_check_count(self):
        for production in self:
            production.fahm_qc_check_count = len(production.fahm_qc_check_ids)

    @api.depends("fahm_qc_check_ids.state", "fahm_qc_check_ids.date",
                 "fahm_qc_check_ids.check_type", "product_id.fahm_qc_required")
    def _compute_fahm_qc_state(self):
        for production in self:
            checks = production.fahm_qc_check_ids.filtered(
                lambda c: c.check_type == "finished_product"
            )
            # The most recent evaluation decides the release.
            latest = checks.sorted(lambda c: (c.date, c.id), reverse=True)[:1]
            if latest.state in ("passed", "failed"):
                production.fahm_qc_state = latest.state
            elif production.product_id.fahm_qc_required or checks:
                production.fahm_qc_state = "pending"
            else:
                production.fahm_qc_state = "not_required"

    @api.depends("state", "qty_producing", "product_uom_id", "product_id.fahm_volume_per_unit")
    def _compute_fahm_production_figures(self):
        for production in self:
            if production.state != "done":
                production.fahm_qty_produced = 0.0
                production.fahm_liters_produced = 0.0
                continue
            qty = production.product_uom_id._compute_quantity(
                production.qty_producing, production.product_id.uom_id
            )
            production.fahm_qty_produced = qty
            production.fahm_liters_produced = qty * production.product_id.fahm_volume_per_unit

    @api.depends("scrap_ids.state", "scrap_ids.scrap_qty", "scrap_ids.product_id")
    def _compute_fahm_reject_qty(self):
        for production in self:
            scraps = production.scrap_ids.filtered(
                lambda s: s.state == "done" and s.product_id == production.product_id
            )
            production.fahm_reject_qty = sum(
                s.product_uom_id._compute_quantity(s.scrap_qty, s.product_id.uom_id) for s in scraps
            )

    def action_fahm_create_qc_check(self):
        self.ensure_one()
        if self.state in ("draft", "cancel"):
            raise UserError(self.env._("Confirm the manufacturing order before creating a QC check."))
        check = self.env["fahm.water.qc.check"].create({
            "check_type": "finished_product",
            "production_id": self.id,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "fahm.water.qc.check",
            "res_id": check.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_fahm_view_qc_checks(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "fahm_water_manufacturing.action_fahm_water_qc_check"
        )
        action["domain"] = [("production_id", "=", self.id)]
        action["context"] = {
            "default_production_id": self.id,
            "default_check_type": "finished_product",
        }
        if self.fahm_qc_check_count == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = self.fahm_qc_check_ids.id
        return action

    # ------------------------------------------------------------------
    # Step-by-step flow: materials -> approval -> store issue -> stages -> QC -> close
    # ------------------------------------------------------------------
    def _fahm_is_water_order(self):
        self.ensure_one()
        return self.product_id.fahm_water_type == "finished_water"

    @api.depends("fahm_material_request_ids.state")
    def _compute_fahm_material_state(self):
        for production in self:
            requests = production.fahm_material_request_ids.filtered(
                lambda r: r.state not in ("cancel", "rejected"))
            if requests.filtered(lambda r: r.state == "issued"):
                # once a first issue has happened, production can run; top-up requests
                # for additional quantities do not send the order back to step 2
                production.fahm_material_state = "issued"
                continue
            latest = requests.sorted(lambda r: (r.date, r.id), reverse=True)[:1]
            production.fahm_material_state = latest.state if latest else "none"

    def _fahm_material_shortfalls(self):
        """(move, missing qty in move UoM) for components not yet available on the floor."""
        self.ensure_one()
        result = []
        floor = self.location_src_id
        MoveLine = self.env["stock.move.line"]
        for move in self.move_raw_ids.filtered(lambda m: m.state not in ("done", "cancel")):
            product = move.product_id
            # what this order needs: the planned quantity, or more if more is being produced
            needed = max(move.product_uom_qty, move.should_consume_qty)
            # what is physically on the floor for this order: stock there minus what
            # other open orders have reserved
            on_hand = product.with_context(location=floor.id).qty_available
            reserved_by_others = sum(MoveLine.search([
                ("location_id", "child_of", floor.id),
                ("product_id", "=", product.id),
                ("state", "not in", ("done", "cancel")),
                ("move_id.raw_material_production_id", "not in", [False, self.id]),
            ]).mapped("quantity_product_uom"))
            available = product.uom_id._compute_quantity(
                max(on_hand - reserved_by_others, 0.0), move.product_uom)
            missing = needed - available
            if move.product_uom.compare(missing, 0) > 0:
                result.append((move, move.product_uom.round(missing)))
        return result

    @api.depends("state", "fahm_material_state", "qty_producing", "move_raw_ids.quantity",
                 "move_raw_ids.product_uom_qty", "fahm_material_request_ids.state")
    def _compute_fahm_materials_short(self):
        for production in self:
            open_topup = production.fahm_material_request_ids.filtered(
                lambda r: r.state in ("draft", "submitted", "approved", "issuing"))
            production.fahm_materials_short = bool(
                production.state in ("confirmed", "progress", "to_close")
                and production.fahm_material_state == "issued"
                and not open_topup
                and production._fahm_is_water_order()
                and production._fahm_material_shortfalls()
            )

    @api.depends("fahm_material_request_ids")
    def _compute_fahm_material_request_count(self):
        for production in self:
            production.fahm_material_request_count = len(production.fahm_material_request_ids)

    @api.depends("state", "fahm_material_state", "fahm_stage_line_ids.state",
                 "fahm_qc_state", "product_id.fahm_water_type", "fahm_handover_state")
    def _compute_fahm_step(self):
        for production in self:
            if production.state == "draft":
                step = "plan"
            elif production.state == "cancel":
                step = "cancel"
            elif production.state == "done":
                step = "done" if production.fahm_handover_state in ("received", "none_needed") \
                    or not production._fahm_is_water_order() else "handover"
            elif not production._fahm_is_water_order():
                step = False
            elif production.fahm_material_state in ("none", "draft"):
                step = "materials"
            elif production.fahm_material_state == "submitted":
                step = "approval"
            elif production.fahm_material_state in ("approved", "issuing"):
                step = "issue"
            elif production.fahm_stage_line_ids.filtered(lambda l: l.state != "done") \
                    or not production.fahm_stage_line_ids:
                step = "production"
            elif production.fahm_qc_required and production.fahm_qc_state != "passed":
                step = "qc"
            else:
                step = "close"
            production.fahm_step = step

    def action_fahm_request_materials(self):
        """Request the components still missing on the production floor. The first request
        asks for everything; later ones (e.g. after producing more than planned) only for
        the shortfall."""
        self.ensure_one()
        if self.state not in ("confirmed", "progress", "to_close"):
            raise UserError(self.env._("Confirm the manufacturing order before requesting materials."))
        open_request = self.fahm_material_request_ids.filtered(
            lambda r: r.state in ("draft", "submitted", "approved", "issuing"))[:1]
        if not open_request:
            shortfalls = self._fahm_material_shortfalls()
            if not shortfalls:
                raise UserError(self.env._(
                    "All components of %s are already available on the production floor.", self.name))
            open_request = self.env["fahm.material.request"].create({
                "production_id": self.id,
                "company_id": self.company_id.id,
                "note": self.env._("Additional materials: the order needs more than was issued.")
                if self.fahm_material_state == "issued" else False,
                "line_ids": [
                    fields.Command.create({
                        "product_id": move.product_id.id,
                        "uom_id": move.product_uom.id,
                        "qty_required": max(move.product_uom_qty, move.should_consume_qty),
                        "qty_requested": missing,
                        "sequence": index,
                    })
                    for index, (move, missing) in enumerate(shortfalls, start=1)
                ],
            })
        request = open_request
        return {
            "type": "ir.actions.act_window",
            "res_model": "fahm.material.request",
            "res_id": request.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # Finished goods: staging -> handover to the store
    # ------------------------------------------------------------------
    @api.depends("state", "location_dest_id", "fahm_handover_ids.state", "product_id.fahm_water_type")
    def _compute_fahm_handover_state(self):
        for production in self:
            handovers = production.fahm_handover_ids.filtered(lambda h: h.state != "cancel")
            staging = production.picking_type_id.warehouse_id.fahm_fg_location_id
            if production.state != "done" or not production._fahm_is_water_order() \
                    or not staging or production.location_dest_id != staging:
                production.fahm_handover_state = "none_needed"
            elif handovers.filtered(lambda h: h.state in ("draft", "submitted")):
                production.fahm_handover_state = "submitted"
            elif handovers.filtered(lambda h: h.state == "received"):
                production.fahm_handover_state = "received"
            else:
                production.fahm_handover_state = "to_hand_over"

    @api.depends("fahm_handover_ids")
    def _compute_fahm_handover_count(self):
        for production in self:
            production.fahm_handover_count = len(production.fahm_handover_ids)

    def action_fahm_handover(self):
        """Create the finished goods handover (staging -> store) for a done order."""
        self.ensure_one()
        if self.state != "done":
            raise UserError(self.env._("Finish the production order before handing over the goods."))
        open_handover = self.fahm_handover_ids.filtered(lambda h: h.state in ("draft", "submitted"))[:1]
        handover = open_handover or self.env["fahm.fg.handover"]._create_from_production(self)
        return {
            "type": "ir.actions.act_window",
            "res_model": "fahm.fg.handover",
            "res_id": handover.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_fahm_view_handovers(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "fahm_water_manufacturing.action_fahm_fg_handover")
        action["domain"] = [("production_id", "=", self.id)]
        if self.fahm_handover_count == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = self.fahm_handover_ids.id
        return action

    def action_fahm_view_material_requests(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "fahm_water_manufacturing.action_fahm_material_request")
        action["domain"] = [("production_id", "=", self.id)]
        action["context"] = {"default_production_id": self.id}
        if self.fahm_material_request_count == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = self.fahm_material_request_ids.id
        return action

    def _fahm_load_stages(self):
        Stage = self.env["fahm.production.stage"]
        for production in self:
            if not production._fahm_is_water_order() or production.fahm_stage_line_ids:
                continue
            line_type = production.fahm_line_id.fahm_line_type
            if line_type not in ("bottle", "jar"):
                line_type = "bottle"
            stages = Stage.search([("line_type", "=", line_type)])
            production.fahm_stage_line_ids = [
                fields.Command.create({"stage_id": stage.id, "sequence": stage.sequence})
                for stage in stages
            ]
        return True

    def action_fahm_load_stages(self):
        self._fahm_check_can_produce()
        return self._fahm_load_stages()

    def _fahm_check_can_produce(self):
        for production in self:
            if not production._fahm_is_water_order():
                continue
            if production.state not in ("confirmed", "progress", "to_close"):
                raise UserError(self.env._("%s is not confirmed.", production.name))
            if production.fahm_material_state != "issued":
                raise UserError(self.env._(
                    "%s cannot start yet: its materials must first be requested, "
                    "approved and issued by the store.", production.name))

    def action_start(self):
        self._fahm_check_can_produce()
        return super().action_start()

    def pre_button_mark_done(self):
        self._fahm_check_can_produce()
        unfinished = self.filtered(
            lambda p: p.fahm_stage_line_ids.filtered(lambda l: l.state != "done"))
        if unfinished:
            raise UserError(self.env._(
                "Finish all production stages before closing: %s",
                ", ".join(unfinished.mapped("name"))))
        blocked = self.filtered(lambda p: p.fahm_qc_required and p.fahm_qc_state != "passed")
        if blocked:
            raise UserError(self.env._(
                "These manufacturing orders need a passed finished-product QC check "
                "before they can be marked as done:\n%s",
                "\n".join(
                    f"- {p.name} (QC: {dict(p._fields['fahm_qc_state']._description_selection(self.env)).get(p.fahm_qc_state)})"
                    for p in blocked
                ),
            ))
        return super().pre_button_mark_done()
