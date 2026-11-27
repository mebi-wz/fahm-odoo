from odoo import Command
from odoo.exceptions import UserError
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
class TestShortagePurchase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)], limit=1)
        cls.warehouse._fahm_ensure_water_setup()
        litre = cls.env.ref("uom.product_uom_litre")
        cls.preform = _create_product(cls.env, {
            "name": "PR Test Preform", "is_storable": True,
            "fahm_water_type": "preform", "standard_price": 2.5,
        })
        cls.cap = _create_product(cls.env, {
            "name": "PR Test Cap", "is_storable": True,
            "fahm_water_type": "cap", "standard_price": 0.8,
        })
        cls.treated_water = _create_product(cls.env, {
            "name": "PR Test Treated Water", "is_storable": True, "uom_id": litre.id,
            "fahm_water_type": "treated_water",
        })
        cls.water = _create_product(cls.env, {
            "name": "PR Test Water Pack", "is_storable": True,
            "fahm_water_type": "finished_water", "fahm_qc_required": True,
        })
        cls.bom = cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.water.product_tmpl_id.id,
            "product_qty": 1,
            "bom_line_ids": [
                Command.create({"product_id": cls.preform.id, "product_qty": 24}),
                Command.create({"product_id": cls.cap.id, "product_qty": 24}),
                Command.create({"product_id": cls.treated_water.id, "product_qty": 12,
                                "product_uom_id": litre.id}),
            ],
        })
        stock = cls.warehouse.lot_stock_id
        # preforms: 100 in store (need 240) -> short 140; caps: plenty; water: none in tank
        cls.env["stock.quant"]._update_available_quantity(cls.preform, stock, 100)
        cls.env["stock.quant"]._update_available_quantity(cls.cap, stock, 1000)

    def _request(self, qty=10):
        mo_form = Form(self.env["mrp.production"])
        mo_form.product_id = self.water
        mo_form.bom_id = self.bom
        mo_form.product_qty = qty
        mo = mo_form.save()
        mo.action_confirm()
        return self.env["fahm.material.request"].browse(mo.action_fahm_request_materials()["res_id"])

    def test_purchase_request_for_missing_quantity(self):
        request = self._request(qty=10)
        self.assertTrue(request.has_shortage)
        action = request.action_create_purchase_request()
        pr = self.env["local.purchase.requisition"].browse(action["res_id"])
        self.assertEqual(pr.fahm_material_request_id, request)
        self.assertEqual(pr.state, "draft")
        # only the preform is bought: caps are in store, treated water comes from treatment
        self.assertEqual(pr.line_ids.product_id, self.preform)
        self.assertAlmostEqual(pr.line_ids.quantity, 140)
        self.assertAlmostEqual(pr.line_ids.unit_price, 2.5)
        self.assertEqual(request.purchase_request_count, 1)

        # running it again refreshes the same draft instead of creating a duplicate
        request.action_create_purchase_request()
        self.assertEqual(request.purchase_request_count, 1)
        self.assertEqual(len(pr.line_ids), 1)

    def test_only_water_short(self):
        self.env["stock.quant"]._update_available_quantity(
            self.preform, self.warehouse.lot_stock_id, 1000)
        request = self._request(qty=1)
        self.assertTrue(request.has_shortage)  # treated water is missing in the tank
        with self.assertRaises(UserError):
            request.action_create_purchase_request()

    def test_no_shortage(self):
        self.env["stock.quant"]._update_available_quantity(
            self.preform, self.warehouse.lot_stock_id, 1000)
        self.env["stock.quant"]._update_available_quantity(
            self.treated_water, self.warehouse.fahm_tank_location_id, 1000)
        request = self._request(qty=1)
        self.assertFalse(request.has_shortage)
        with self.assertRaises(UserError):
            request.action_create_purchase_request()

    def _user(self, login, groups):
        return self.env["res.users"].create({
            "name": login, "login": login,
            "group_ids": [Command.set([self.env.ref(g).id for g in ["base.group_user", *groups]])],
        })

    def test_production_user_without_purchase_access(self):
        """A production user with no purchase role can open a material request that has a
        purchase request, sees the count, but no purchase button."""
        request = self._request(qty=10)
        request.action_create_purchase_request()
        user = self._user("fahm_prod_only", ["mrp.group_mrp_user"])
        request_as_user = request.with_user(user)
        form = Form(request_as_user)  # loads the form view fields as this user
        self.assertEqual(form.purchase_request_count, 1)
        self.assertFalse(form.can_purchase)
        self.assertTrue(form.has_shortage)
        with self.assertRaises(UserError):
            request_as_user.action_create_purchase_request()
        # the list view also loads for this user
        self.env["fahm.material.request"].with_user(user).web_search_read(
            [("id", "=", request.id)],
            {"name": {}, "state": {}, "purchase_request_count": {}})

    def test_procurement_user_without_manufacturing_access(self):
        """Procurement staff can open the purchase requisition and see where it comes from
        without access to manufacturing."""
        request = self._request(qty=10)
        pr = self.env["local.purchase.requisition"].browse(
            request.action_create_purchase_request()["res_id"])
        user = self._user("fahm_proc_only", [
            "procurement_local_purchase.group_local_purchase_pr_manager",
            "purchase.group_purchase_user",
        ])
        form = Form(pr.with_user(user))
        self.assertIn(request.name, form.fahm_source_reference)
        self.assertIn(request.production_id.name, form.fahm_source_reference)
