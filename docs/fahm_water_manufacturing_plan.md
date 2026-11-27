# Fahm Water – Manufacturing Implementation Plan

Odoo 19 (Community) · Module: `fahm_water_manufacturing` · Author: Mehbuba

## 1. Business context

Fahm produces packaged drinking water. A typical day looks like this:

1. **Raw water** is drawn from a source (borehole, spring or municipal line).
2. Water goes through **treatment**: sand/carbon filtration → softening → reverse osmosis (RO) → mineral dosing → ozonation / UV.
3. **Blow moulding** turns PET preforms into bottles (or 20 L jars are washed and sanitised).
4. **Filling – capping – labelling – shrink wrapping** on a production line, per shift.
5. **Quality control** samples every batch (physical, chemical and microbiological tests) before release.
6. Finished packs move to the finished-goods warehouse and on to sales and distribution.

Odoo's standard Manufacturing (MRP) app already covers bills of materials, manufacturing orders, component consumption, work centres, lots and scrap. The custom module adds what is specific to a water plant.

## 2. Scope

### Phase 1 (built now)

| Area | What it does | How |
|---|---|---|
| Product master | Mark each product as finished water, treated water, preform, cap, label, shrink film, carton, 20 L jar, etc. Record litres per unit, bottles per pack and whether QC release is required. | Fields on `product.template` |
| Production lines | Flag work centres as water lines, with line type (small bottle / 20 L jar) and rated capacity (units/hour). | Fields on `mrp.workcenter` |
| Water sources | Register boreholes/springs/municipal supply, with location and licence details. | New model `fahm.water.source` |
| Shifts | Morning / Afternoon / Night shifts with hours, picked on each MO. | New model `fahm.production.shift` |
| QC parameters | Configurable test library with limits, per stage (raw water, treated water, finished product). Defaults: pH, TDS, turbidity, conductivity, residual ozone, hardness, total coliform, E. coli, appearance, odour, taste, fill level, cap seal, label, date coding. | New model `fahm.water.qc.parameter` |
| QC checks | QC records for raw water, treated water and finished batches. Lines are filled from the parameter library, results are evaluated automatically against limits and the check passes or fails. Each check has a printable Certificate of Analysis. | New model `fahm.water.qc.check` + PDF report |
| MO extension | Line, shift and operators on the MO; a button that creates the finished-product QC check; QC status; **an MO for a QC-required product cannot be marked Done until its QC check has passed**; litres produced; wastage (scrap) totals. | Inherit `mrp.production` |
| Wastage | Uses Odoo's standard *Scrap* on the MO (keeps stock correct), with water-plant scrap reasons (blow-moulding defect, underfill, leakage, cap defect, label defect, contamination…). | Data for `stock.scrap.reason.tag` |
| Expiry / lot traceability | Finished water tracked by lot with best-before date. | Standard `product_expiry` (dependency) |
| Analysis | Production analysis (pivot/graph/list) by day, product, line and shift: planned vs produced quantity, litres, scrap and efficiency. | SQL view `fahm.water.production.report` |
| Security | Production uses the standard Manufacturing groups (User / Administrator). A new **Water Quality Control** privilege has two levels: Inspector (records and evaluates checks) and Manager (configures parameters and reopens evaluated checks). Production users can create checks but cannot enter or evaluate results. | `res.groups` + access rules |

### Phase 1b – step-by-step production (built)

Each water production order now follows fixed steps, shown as a bar at the top of the order:

| Step | Who | What happens in Odoo |
|---|---|---|
| 1. Plan | Production supervisor | Create the order (product, quantity, line, shift) and confirm it. |
| 2. Request materials | Production supervisor | **Request Materials** creates a request pre-filled from the bill of materials (treated water, preforms, caps, labels, film). Quantities can be adjusted, then **Submit for Approval**. |
| 3. Approval | Production manager | **Approve** or **Reject** the request (*Manufacturing → Requests to Approve*). |
| 4. Store issue | Store keeper | **Issue Materials** creates the transfers to the *Production Floor*: packaging from the store, treated water from the *Treated Water Tank*. The store validates the transfer when the goods are handed over. |
| 5. Production | Operators | The stages load automatically. Each stage is started and finished in order with good/rejected quantities: bottle line = Blow Moulding → Filling & Capping → Labelling & Coding → Packing; 20 L jar line = Washing & Sanitising → Filling & Capping → Labelling & Coding. The final stage sets the produced quantity. |
| 6. Quality check | QC inspector | Finished-product QC check; must pass. |
| 7. Close | Production supervisor | **Produce** consumes the issued materials and puts the finished packs into **Finished Goods Staging**. |
| 8. Hand over to store | Production → store keeper | **Hand Over to Store** creates a handover (lots and best-before dates) and a transfer from staging to WH/Stock. The store validates it on receipt (*Inventory → Finished Goods to Receive*), and the order is complete. |

**More produced than planned:** when the final stage records more than the planned quantity, the order shows **Request More Materials**. It creates a top-up material request for only what is missing on the production floor, with the same approval and store issue. The order stays in production while the top-up is processed.

**Materials not in store:** materials requested in a larger quantity than the store has are shown in red on the material request. **Create Purchase Request** (module `fahm_water_procurement`) creates a draft *Local Purchase Requisition* for the missing quantities, priced at product cost and linked to the request and the order. Treated water is excluded, because it comes from a water treatment batch. The requisition then follows the normal approvals → RFQ / PO → receipt, and the store issues the materials once they arrive. Users need the *Store Requisition: Requester* role to create it.

The order cannot start before the materials are issued, and it cannot be closed before all stages are finished and QC has passed.

**Raw water / water treatment:** *Manufacturing → Water Treatment*. A treatment batch records the raw water meter readings (start/end), the treated water produced, the reject water and the recovery %. The treated water must pass a *Treated Water* QC check. Only then does **Move to Tank** put it into the *Treated Water Tank*, where production can request it.

**Set-up created automatically per warehouse:** the *Treated Water Tank* and *Production Floor* locations, and the *Water Production* operation type. Bills of materials for finished water are linked to that operation type, so orders consume from the production floor.

### Phase 2 (next)

- **20 L jar returns / deposits**: track jars issued to and returned by customers, deposit amounts and jar wash cycles.
- **Maintenance**: preventive maintenance for blow-moulders, fillers, RO membranes and ozone generators, using the standard `maintenance` app linked to the lines.
- **Costing**: enable `mrp_account` and set work-centre hourly costs so each pack carries its true cost (water, power, labour, packaging).
- **Packaging UoM**: sell in packs (6 / 12 / 24) using UoM categories, with pallet configuration.
- **Dashboard**: add production KPIs (litres per day, efficiency, QC pass rate, wastage %) to `fahm_dashboard`.
- **Regulatory**: align parameter limits with the applicable national standard for packaged drinking water and the regulator's audit format.
- **Sales & distribution**: route/van sales and delivery planning.

## 3. Data model (Phase 1)

```
product.template
  + fahm_water_type        (finished_water / treated_water / preform / cap / label / shrink_film / carton / jar / chemical / other)
  + fahm_volume_per_unit   (litres per unit of the product's UoM)
  + fahm_units_per_pack    (bottles per pack, informational)
  + fahm_qc_required       (MO needs a passed QC check before Done)

mrp.workcenter
  + fahm_is_water_line, fahm_line_type, fahm_rated_capacity

fahm.water.source        name, code, source_type, location, licence no., licence expiry, notes
fahm.production.shift    name, start hour, end hour, sequence
fahm.water.qc.parameter  name, stage, test_type (measure / pass_fail), uom label, min, max, method, sequence
fahm.water.qc.check      name (WQC/…), check_type, date, production_id, product_id, lot_id, source_id,
                         inspector_id, line_ids, state (draft / passed / failed), notes
fahm.water.qc.check.line parameter_id, value, pass_fail_result, result (pass / fail / pending)

mrp.production
  + fahm_line_id, fahm_shift_id, fahm_operator_ids
  + fahm_qc_required, fahm_qc_check_ids, fahm_qc_state (not_required / pending / passed / failed)
  + fahm_qty_produced, fahm_liters_produced, fahm_scrap_qty

fahm.water.production.report (SQL view over done MOs + scraps)
```

## 4. Process flow in Odoo

1. **Set-up (one time):** products and BoMs (e.g. *Fahm Water 0.5 L × 24* = 24 preforms + 24 caps + 24 labels + shrink film + 12 L treated water), water lines, shifts, QC parameter limits and water sources.
2. **Daily water checks:** the QC inspector records *Raw Water* and *Treated Water* checks per source and treatment stage.
3. **Production:** a planner creates an MO, chooses the line, shift and operators, then confirms it. Components are reserved.
4. **Run:** operators record the produced quantity and the lot. Rejects are recorded with *Scrap* and a reason.
5. **Release:** QC clicks *Create QC Check* on the MO, enters the results and evaluates them. If the check passes, the MO can be marked Done. If it fails, the MO is blocked and the lot is held.
6. **Reporting:** a Certificate of Analysis is printed per batch, and managers review the Production Analysis.

## 5. Delivery steps

| # | Step | Owner |
|---|---|---|
| 1 | Confirm the product list, pack sizes, BoMs and QC limits with Fahm production/QC staff | Business + Mehbuba |
| 2 | Build and unit-test Phase 1 module | Dev |
| 3 | Load master data (products, BoMs, lines, shifts, parameters) | Dev + Business |
| 4 | UAT: run 2–3 days of real production in a test database | Production + QC |
| 5 | Train users (operators, QC, managers) | Dev |
| 6 | Go-live, then start Phase 2 | All |

## 6. Assumptions to confirm with Fahm

- Products and pack sizes (0.33 L, 0.5 L, 1 L, 1.5 L, 5 L, 20 L jar?)
- Whether preforms are bought or moulded in-house (in-house moulding would need a resin → preform BoM)
- QC limits and which tests are done in-house vs at an external lab
- Number of production lines and shifts
- Whether a QC manager may release a batch despite a failed result (currently **not** allowed)
