from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestWaterManufacturing(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.preform = cls.env["product.product"].create({
            "name": "Test Preform", "is_storable": True, "fahm_water_type": "preform",
        })
        cls.water = cls.env["product.product"].create({
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
            "bom_line_ids": [Command.create({"product_id": cls.preform.id, "product_qty": 24})],
        })
        cls.env["stock.quant"]._update_available_quantity(cls.preform, cls.stock_location, 10000)
        cls.line = cls.env["mrp.workcenter"].create({
            "name": "Test Bottle Line", "fahm_is_water_line": True, "fahm_line_type": "bottle",
        })

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

    def test_qc_check_loads_parameters_per_stage(self):
        check = self.env["fahm.water.qc.check"].create({
            "check_type": "finished_product", "product_id": self.water.id,
        })
        params = check.line_ids.parameter_id
        self.assertTrue(params)
        self.assertTrue(all(p.stage_finished_product for p in params))
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

    def test_mo_blocked_until_qc_passed(self):
        mo = self._create_mo()
        self.assertEqual(mo.fahm_qc_state, "pending")
        mo.qty_producing = 10
        with self.assertRaises(UserError):
            mo.button_mark_done()

        action = mo.action_fahm_create_qc_check()
        check = self.env["fahm.water.qc.check"].browse(action["res_id"])
        self.assertEqual(check.production_id, mo)
        self.assertEqual(check.product_id, self.water)

        self._fill_check(check, passing=False)
        check.action_evaluate()
        self.assertEqual(mo.fahm_qc_state, "failed")
        with self.assertRaises(UserError):
            mo.button_mark_done()

        check2 = self.env["fahm.water.qc.check"].create({
            "check_type": "finished_product", "production_id": mo.id,
        })
        self._fill_check(check2, passing=True)
        check2.action_evaluate()
        self.assertEqual(mo.fahm_qc_state, "passed")

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

    def test_mo_without_qc_requirement(self):
        self.water.fahm_qc_required = False
        mo = self._create_mo(qty=2)
        self.assertEqual(mo.fahm_qc_state, "not_required")
        mo.qty_producing = 2
        mo.button_mark_done()
        self.assertEqual(mo.state, "done")

    def test_parameter_limit_validation(self):
        with self.assertRaises(ValidationError):
            self.env["fahm.water.qc.parameter"].create({
                "name": "Bad", "test_type": "measure",
                "has_min": True, "min_value": 5, "has_max": True, "max_value": 1,
            })
