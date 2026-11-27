{
    "name": "Fahm Water Procurement",
    "version": "19.0.1.0.0",
    "category": "Supply Chain/Manufacturing",
    "summary": "Create a local purchase request when production materials are not available in store",
    "description": """
Bridge between Fahm Water Manufacturing and Local Procurement.

When a production material request asks for more than the store has, the
requester or the store keeper can create a Local Purchase Requisition for the
missing quantities in one click. The requisition then follows the normal
procurement approvals (department, PR manager, CEO), RFQ / PO and receipt.
Once the goods are received, the store issues them to production.
""",
    "author": "Mehbuba",
    "license": "LGPL-3",
    "depends": [
        "fahm_water_manufacturing",
        "procurement_local_purchase",
    ],
    "data": [
        "views/material_request_views.xml",
        "views/local_purchase_views.xml",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
