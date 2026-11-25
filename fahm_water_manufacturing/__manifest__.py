{
    "name": "Fahm Water Manufacturing",
    "version": "19.0.1.1.0",
    "category": "Supply Chain/Manufacturing",
    "summary": "Packaged drinking water production: lines, shifts, water quality control and batch release",
    "description": """
Fahm Water Manufacturing
========================
Extends Odoo Manufacturing for a packaged drinking water plant:

* Step-by-step production: plan → request materials → manager approval →
  store issue → production stages → QC → close
* Water treatment batches (meter readings, treated water into the tank after QC)
* Water product classification (finished water, preforms, caps, labels, ...)
* Production lines and shifts on manufacturing orders
* Water sources (borehole, spring, municipal)
* Configurable water quality parameters with limits per stage
* QC checks for raw water, treated water and finished batches
* Batch release: MOs for QC-required products cannot be closed before QC passes
* Certificate of Analysis (PDF)
* Production analysis: litres, efficiency, rejects by line and shift
""",
    "author": "Mehbuba",
    "license": "LGPL-3",
    "depends": [
        "mrp",
        "product_expiry",
    ],
    "data": [
        "security/water_security.xml",
        "security/ir.model.access.csv",
        "data/sequence_data.xml",
        "data/shift_data.xml",
        "data/qc_parameter_data.xml",
        "data/scrap_reason_data.xml",
        "data/production_stage_data.xml",
        "report/qc_certificate_report.xml",
        "views/product_views.xml",
        "views/mrp_workcenter_views.xml",
        "views/water_source_views.xml",
        "views/production_shift_views.xml",
        "views/qc_parameter_views.xml",
        "views/qc_check_views.xml",
        "views/water_treatment_views.xml",
        "views/material_request_views.xml",
        "views/production_stage_views.xml",
        "views/mrp_production_views.xml",
        "views/production_report_views.xml",
        "views/menus.xml",
    ],
    "demo": [
        "demo/water_demo.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
