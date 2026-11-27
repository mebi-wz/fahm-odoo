from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged


def _create_product(env, vals):
    """Create a test product with a unique code (some installed modules require
    a product code and part number on every product)."""
    code = "T-" + vals["name"].upper().replace(" ", "-")
    vals = dict(vals, default_code=code)
    if "part_number" in env["product.product"]._fields:
        vals["part_number"] = code
    return env["product.product"].create(vals)


@tagged("post_install", "-at_install")
class TestWaterManufacturing(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)], limit=1)
        cls.warehouse._fahm_ensure_water_setup()
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.litre = cls.env.ref("uom.product_uom_litre")
        cls.preform = _create_product(cls.env, {
            "name": "Test Preform", "is_storable": True, "fahm_water_type": "preform",
        })
        cls.treated_water = _create_product(cls.env, {
            "name": "Test Treated Water", "is_storable": True, "uom_id": cls.litre.id,
            "fahm_water_type": "treated_water", "fahm_volume_per_unit": 1,
        })
        cls.water = _create_product(cls.env, {
            "name": "Test Water 0.5 L x 24",
            "is_storable": True,
            "fahm_water_type": "finished_water",
            "fahm_volume_per_unit": 12,
            "fahm_units_per_pack": 24,
            "fahm_qc_required": True,
        })
        cls.bom = cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.water.product_tmpl_id.id,
            "product_qty": 1,
            "bom_line_ids": [
                Command.create({"product_id": cls.preform.id, "product_qty": 24}),
                Command.create({"product_id": cls.treated_water.id, "product_qty": 12,
                                "product_uom_id": cls.litre.id}),
            ],
        })
        cls.env["stock.quant"]._update_available_quantity(cls.preform, cls.stock_location, 10000)
        cls.env["stock.quant"]._update_available_quantity(
            cls.treated_water, cls.warehouse.fahm_tank_location_id, 5000)
        cls.line = cls.env["mrp.workcenter"].create({
            "name": "Test Bottle Line", "fahm_is_water_line": True, "fahm_line_type": "bottle",
        })
        cls.source = cls.env["fahm.water.source"].create({"name": "Test Borehole", "code": "TBH"})

    # ------------------------------------------------------------------ helpers
    def _create_mo(self, qty=10):
        mo_form = Form(self.env["mrp.production"])
        mo_form.product_id = self.water
        mo_form.bom_id = self.bom
        mo_form.product_qty = qty
        mo = mo_form.save()
        mo.fahm_line_id = self.line
        mo.fahm_shift_id = self.env.ref("fahm_water_manufacturing.shift_morning")
        mo.action_confirm()
        return mo

    def _fill_check(self, check, passing=True):
        for line in check.line_ids:
            param = line.parameter_id
            if param.test_type == "pass_fail":
                line.pass_fail_result = "pass" if passing else "fail"
            else:
                if param.has_min and param.has_max:
                    value = (param.min_value + param.max_value) / 2
                elif param.has_min:
                    value = param.min_value
                else:
                    value = param.max_value
                line.write({"value": value, "is_measured": True})

    def _issue_materials(self, mo):
        action = mo.action_fahm_request_materials()
        request = self.env["fahm.material.request"].browse(action["res_id"])
        request.action_submit()
        request.action_approve()
        request.action_issue()
        for picking in request.picking_ids:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
            picking.move_ids.picked = True
            picking.button_validate()
        return request

    def _run_stages(self, mo, packs):
        mo._fahm_load_stages()
        for line in mo.fahm_stage_line_ids:
            line.action_start()
            line.qty_good = packs if line.is_final else packs * 24
            line.action_finish()

    def _pass_qc(self, mo):
        action = mo.action_fahm_create_qc_check()
        check = self.env["fahm.water.qc.check"].browse(action["res_id"])
        self._fill_check(check, passing=True)
        check.action_evaluate()
        return check

    # ------------------------------------------------------------------ setup
    def test_water_bom_uses_water_operation(self):
        self.assertEqual(self.bom.picking_type_id, self.warehouse.fahm_production_type_id)
        mo = self._create_mo()
        self.assertEqual(mo.location_src_id, self.warehouse.fahm_floor_location_id)

    # ------------------------------------------------------------------ QC
    def test_qc_check_loads_parameters_per_stage(self):
        check = self.env["fahm.water.qc.check"].create({
            "check_type": "finished_product", "product_id": self.water.id,
        })
        self.assertTrue(check.line_ids)
        self.assertTrue(all(p.stage_finished_product for p in check.line_ids.parameter_id))
        self.assertTrue(check.name.startswith("WQC/"))

    def test_measurement_evaluation(self):
        ph = self.env.ref("fahm_water_manufacturing.qc_param_ph")
        check = self.env["fahm.water.qc.check"].create({
            "check_type": "treated_water",
            "line_ids": [Command.create({"parameter_id": ph.id})],
        })
        line = check.line_ids
        self.assertEqual(line.result, "pending")
        line.value = 9.2
        self.assertEqual(line.result, "fail")
        line.value = 7.1
        self.assertEqual(line.result, "pass")
        coliform = self.env.ref("fahm_water_manufacturing.qc_param_coliform")
        zero_line = self.env["fahm.water.qc.check.line"].create({
            "check_id": check.id, "parameter_id": coliform.id, "value": 0,
        })
        self.assertEqual(zero_line.result, "pending", "A 0 value must be confirmed as measured")
        zero_line.is_measured = True
        self.assertEqual(zero_line.result, "pass")

    def test_evaluate_requires_all_results(self):
        check = self.env["fahm.water.qc.check"].create({"check_type": "treated_water"})
        with self.assertRaises(UserError):
            check.action_evaluate()
        self._fill_check(check)
        check.action_evaluate()
        self.assertEqual(check.state, "passed")
        with self.assertRaises(UserError):
            check.line_ids[0].value = 1

    def test_parameter_limit_validation(self):
        with self.assertRaises(ValidationError):
            self.env["fahm.water.qc.parameter"].create({
                "name": "Bad", "test_type": "measure",
                "has_min": True, "min_value": 5, "has_max": True, "max_value": 1,
            })

    # ------------------------------------------------------------------ treatment
    def test_treatment_batch_to_tank_after_qc(self):
        tank = self.warehouse.fahm_tank_location_id
        before = self.treated_water.with_context(location=tank.id).qty_available
        batch = self.env["fahm.water.treatment"].create({
            "source_id": self.source.id,
            "product_id": self.treated_water.id,
            "warehouse_id": self.warehouse.id,
            "meter_start": 1000, "meter_end": 3000, "treated_qty": 1500,
        })
        self.assertEqual(batch.raw_water_qty, 2000)
        self.assertEqual(batch.reject_qty, 500)
        self.assertAlmostEqual(batch.recovery_rate, 75)
        batch.action_submit_qc()
        self.assertEqual(batch.state, "qc")
        with self.assertRaises(UserError):
            batch.action_done()
        check = batch.qc_check_ids
        self.assertEqual(check.check_type, "treated_water")
        self._fill_check(check)
        check.action_evaluate()
        self.assertEqual(batch.qc_state, "passed")
        batch.action_done()
        self.assertEqual(batch.state, "done")
        after = self.treated_water.with_context(location=tank.id).qty_available
        self.assertAlmostEqual(after - before, 1500)

    def test_treated_water_cannot_exceed_raw(self):
        with self.assertRaises(ValidationError):
            self.env["fahm.water.treatment"].create({
                "source_id": self.source.id, "product_id": self.treated_water.id,
                "meter_start": 0, "meter_end": 100, "treated_qty": 200,
            })

    # ------------------------------------------------------------------ material request
    def test_material_request_flow(self):
        mo = self._create_mo(qty=10)
        self.assertEqual(mo.fahm_step, "materials")
        with self.assertRaises(UserError):
            mo.action_start()

        action = mo.action_fahm_request_materials()
        request = self.env["fahm.material.request"].browse(action["res_id"])
        self.assertEqual(
            {(l.product_id, l.qty_requested) for l in request.line_ids},
            {(self.preform, 240), (self.treated_water, 120)},
        )
        request.action_submit()
        self.assertEqual(mo.fahm_step, "approval")
        request.action_approve()
        self.assertEqual(mo.fahm_step, "issue")
        request.action_issue()
        self.assertEqual(request.state, "issuing")
        # treated water comes from the tank, preforms from the store: two transfers
        self.assertEqual(len(request.picking_ids), 2)
        self.assertEqual(
            set(request.picking_ids.location_id.ids),
            {self.stock_location.id, self.warehouse.fahm_tank_location_id.id})
        self.assertTrue(all(p.location_dest_id == self.warehouse.fahm_floor_location_id
                            for p in request.picking_ids))
        for picking in request.picking_ids:
            picking.move_ids.picked = True
            picking.button_validate()
        self.assertEqual(request.state, "issued")
        self.assertEqual(mo.fahm_material_state, "issued")
        self.assertEqual(mo.fahm_step, "production")
        self.assertEqual(mo.reservation_state, "assigned")
        self.assertTrue(mo.fahm_stage_line_ids, "Stages are loaded once materials are issued")

    def test_request_cannot_be_issued_before_approval(self):
        mo = self._create_mo()
        request = self.env["fahm.material.request"].browse(mo.action_fahm_request_materials()["res_id"])
        request.action_submit()
        with self.assertRaises(UserError):
            request.action_issue()

    # ------------------------------------------------------------------ stages and closing
    def test_stages_are_sequential(self):
        mo = self._create_mo()
        self._issue_materials(mo)
        first, second = mo.fahm_stage_line_ids[:2]
        with self.assertRaises(UserError):
            second.action_start()
        first.action_start()
        self.assertEqual(mo.state, "progress")
        with self.assertRaises(UserError):
            first.action_finish()  # no good quantity yet
        first.qty_good = 240
        first.action_finish()
        second.action_start()
        self.assertEqual(second.state, "progress")

    def test_full_flow_to_done(self):
        mo = self._create_mo(qty=10)
        self._issue_materials(mo)
        with self.assertRaises(UserError):
            mo.button_mark_done()  # stages not finished

        self._run_stages(mo, packs=10)
        self.assertEqual(mo.qty_producing, 10)
        self.assertEqual(mo.fahm_step, "qc")
        with self.assertRaises(UserError):
            mo.button_mark_done()  # QC not passed

        self._pass_qc(mo)
        self.assertEqual(mo.fahm_step, "close")
        mo.button_mark_done()
        self.assertEqual(mo.state, "done")
        self.assertAlmostEqual(mo.fahm_qty_produced, 10)
        self.assertAlmostEqual(mo.fahm_liters_produced, 120)

        self.env.flush_all()
        report = self.env["fahm.water.production.report"].search([("production_id", "=", mo.id)])
        self.assertEqual(len(report), 1)
        self.assertAlmostEqual(report.liters_produced, 120)
        self.assertEqual(report.line_id, self.line)
        self.assertAlmostEqual(report.efficiency, 100)

    def test_failed_qc_blocks_closing(self):
        mo = self._create_mo(qty=2)
        self._issue_materials(mo)
        self._run_stages(mo, packs=2)
        check = self.env["fahm.water.qc.check"].browse(mo.action_fahm_create_qc_check()["res_id"])
        self._fill_check(check, passing=False)
        check.action_evaluate()
        self.assertEqual(mo.fahm_qc_state, "failed")
        with self.assertRaises(UserError):
            mo.button_mark_done()

    # ------------------------------------------------------------------ finished goods handover
    def test_finished_goods_handover_to_store(self):
        staging = self.warehouse.fahm_fg_location_id
        stock = self.warehouse.lot_stock_id
        mo = self._create_mo(qty=5)
        self.assertEqual(mo.location_dest_id, staging)
        self._issue_materials(mo)
        self._run_stages(mo, packs=5)
        self._pass_qc(mo)
        stock_before = self.water.with_context(location=stock.id).qty_available
        mo.button_mark_done()
        self.assertEqual(self.water.with_context(location=staging.id).qty_available, 5)
        self.assertEqual(mo.fahm_handover_state, "to_hand_over")
        self.assertEqual(mo.fahm_step, "handover")

        handover = self.env["fahm.fg.handover"].browse(mo.action_fahm_handover()["res_id"])
        self.assertAlmostEqual(sum(handover.line_ids.mapped("quantity")), 5)
        handover.action_submit()
        self.assertEqual(mo.fahm_handover_state, "submitted")
        picking = handover.picking_ids
        self.assertEqual(picking.location_id, staging)
        self.assertEqual(picking.location_dest_id, stock)
        picking.button_validate()
        self.assertEqual(handover.state, "received")
        self.assertEqual(mo.fahm_handover_state, "received")
        self.assertEqual(mo.fahm_step, "done")
        self.assertEqual(self.water.with_context(location=staging.id).qty_available, 0)
        self.assertAlmostEqual(self.water.with_context(location=stock.id).qty_available - stock_before, 5)

    # ------------------------------------------------------------------ top-up when more is produced
    def test_overproduction_requests_more_materials(self):
        mo = self._create_mo(qty=1)
        first = self._issue_materials(mo)
        self.assertFalse(mo.fahm_materials_short)
        mo._fahm_load_stages()
        stages = mo.fahm_stage_line_ids
        for line in stages:
            line.action_start()
            line.qty_good = 3 if line.is_final else 72
            line.action_finish()
        # 3 packs produced while 1 was planned: components for 2 more packs are missing
        self.assertEqual(mo.qty_producing, 3)
        self.assertTrue(mo.fahm_materials_short)
        self.assertEqual(mo.fahm_material_state, "issued", "A top-up must not send the order back")

        topup = self.env["fahm.material.request"].browse(mo.action_fahm_request_materials()["res_id"])
        self.assertNotEqual(topup, first)
        self.assertEqual(
            {(l.product_id, l.qty_requested) for l in topup.line_ids},
            {(self.preform, 48), (self.treated_water, 24)},
        )
        self.assertEqual(mo.fahm_material_state, "issued")
        self.assertFalse(mo.fahm_materials_short, "No second top-up while one is open")
        topup.action_submit()
        topup.action_approve()
        topup.action_issue()
        for picking in topup.picking_ids:
            picking.move_ids.picked = True
            picking.button_validate()
        self.assertEqual(topup.state, "issued")
        mo.action_assign()
        self.assertFalse(mo.fahm_materials_short)
        self._pass_qc(mo)
        # producing more than planned asks the user to confirm the extra consumption
        mo.with_context(skip_consumption=True, skip_backorder=True).button_mark_done()
        self.assertEqual(mo.state, "done")
