from odoo import fields, models, tools


class FahmWaterProductionReport(models.Model):
    _name = "fahm.water.production.report"
    _description = "Water Production Analysis"
    _auto = False
    _order = "date desc"
    _rec_name = "production_id"

    _depends = {
        "mrp.production": [
            "state", "date_finished", "product_id", "product_qty", "product_uom_id",
            "fahm_line_id", "fahm_shift_id", "fahm_qty_produced", "fahm_liters_produced",
            "fahm_qc_state", "company_id",
        ],
        "stock.scrap": ["production_id", "product_id", "scrap_qty", "state"],
    }

    production_id = fields.Many2one("mrp.production", string="Manufacturing Order", readonly=True)
    date = fields.Date(readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", string="Product Template", readonly=True)
    line_id = fields.Many2one("mrp.workcenter", string="Production Line", readonly=True)
    shift_id = fields.Many2one("fahm.production.shift", string="Shift", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    qc_state = fields.Selection(
        [("not_required", "Not Required"), ("pending", "Pending"),
         ("passed", "Passed"), ("failed", "Failed")],
        string="QC Status", readonly=True,
    )
    qty_planned = fields.Float(string="Planned Qty", readonly=True)
    qty_produced = fields.Float(string="Produced Qty", readonly=True)
    liters_produced = fields.Float(string="Litres Produced", readonly=True)
    reject_qty = fields.Float(string="Rejected Qty", readonly=True)
    efficiency = fields.Float(
        string="Plan Achievement (%)", readonly=True, aggregator="avg",
        help="Produced quantity as a percentage of the planned quantity.",
    )
    reject_rate = fields.Float(string="Reject Rate (%)", readonly=True, aggregator="avg")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH scrap AS (
                    SELECT s.production_id, s.product_id, SUM(s.scrap_qty) AS qty
                      FROM stock_scrap s
                     WHERE s.state = 'done' AND s.production_id IS NOT NULL
                  GROUP BY s.production_id, s.product_id
                )
                SELECT
                    mo.id AS id,
                    mo.id AS production_id,
                    (mo.date_finished AT TIME ZONE 'UTC')::date AS date,
                    mo.product_id,
                    pp.product_tmpl_id,
                    mo.fahm_line_id AS line_id,
                    mo.fahm_shift_id AS shift_id,
                    mo.company_id,
                    mo.fahm_qc_state AS qc_state,
                    mo.product_uom_qty AS qty_planned,
                    mo.fahm_qty_produced AS qty_produced,
                    mo.fahm_liters_produced AS liters_produced,
                    COALESCE(scrap.qty, 0) AS reject_qty,
                    CASE WHEN mo.product_uom_qty > 0
                         THEN 100.0 * mo.fahm_qty_produced / mo.product_uom_qty
                         ELSE 0 END AS efficiency,
                    CASE WHEN (mo.fahm_qty_produced + COALESCE(scrap.qty, 0)) > 0
                         THEN 100.0 * COALESCE(scrap.qty, 0)
                              / (mo.fahm_qty_produced + COALESCE(scrap.qty, 0))
                         ELSE 0 END AS reject_rate
                  FROM mrp_production mo
                  JOIN product_product pp ON pp.id = mo.product_id
                  JOIN product_template pt ON pt.id = pp.product_tmpl_id
             LEFT JOIN scrap ON scrap.production_id = mo.id AND scrap.product_id = mo.product_id
                 WHERE mo.state = 'done'
                   AND pt.fahm_water_type = 'finished_water'
            )
        """)
