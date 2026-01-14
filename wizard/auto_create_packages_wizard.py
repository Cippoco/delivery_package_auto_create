# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_is_zero


class DeliveryAutoCreatePackagesWizard(models.TransientModel):
    _name = "delivery.auto.create.packages.wizard"
    _description = "Auto create packages (colli) from qty per package on a stock move"

    package_id = fields.Many2one(
        'stock.quant.package', 'Source Package', ondelete='restrict',
        check_company=True,
        domain="[('location_id', '=', location_id)]")

    owner_id = fields.Many2one(
        'res.partner', 'From Owner',
        check_company=True, index='btree_not_null',
        help="When validating the transfer, the products will be taken from this owner.")

    move_id = fields.Many2one(
        comodel_name="stock.move",
        string="Move",
        required=True,
        readonly=True,
    )

    product_id = fields.Many2one(
        comodel_name="product.product",
        related="move_id.product_id",
        readonly=True,
        store=False,
    )
    picking_id = fields.Many2one(
        comodel_name="stock.picking",
        related="move_id.picking_id",
        readonly=True,
        store=False,
    )
    location_id = fields.Many2one(
        comodel_name="stock.location",
        related="move_id.location_id",
        readonly=True,
        store=False,
    )
    location_dest_id = fields.Many2one(
        comodel_name="stock.location",
        related="move_id.location_dest_id",
        readonly=True,
        store=False,
    )
    product_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        related="move_id.product_uom",
        readonly=True,
        store=False,
    )

    qty_per_package = fields.Integer(string="Q.tà per collo", default=1, required=True)

    # Pick From (stock.quant) come UI Odoo
    quant_id = fields.Many2one(
        comodel_name="stock.quant",
        string="Pick From",
        required=True,
    )

    @api.constrains("qty_per_package")
    def _check_qty_per_package(self):
        for wizard in self:
            if wizard.qty_per_package <= 0:
                raise UserError(_("La quantità per collo deve essere maggiore di 0."))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        move_id = res.get("move_id") or self.env.context.get("default_move_id")
        if move_id and "move_id" in fields_list:
            res["move_id"] = move_id

        # Default quant: primo quant disponibile per prodotto nella location del move
        if move_id and "quant_id" in fields_list and not res.get("quant_id"):
            move = self.env["stock.move"].browse(move_id)
            Quant = self.env["stock.quant"].sudo()
            quant = Quant.search([
                ("product_id", "=", move.product_id.id),
                ("location_id", "child_of", move.location_id.id),
                ("available_quantity", ">", 0),
            ], limit=1)
            if quant:
                res["quant_id"] = quant.id
        return res

    def action_generate_packages(self):
        self.ensure_one()

        move = self.move_id
        if not move:
            raise UserError(_("Move non trovato."))

        if move.state in ("done", "cancel"):
            raise UserError(_("Non puoi rigenerare colli su un movimento Done/Cancelled."))

        # Quantità totale richiesta = product_uom_qty (come da richiesta)
        total_qty = move.product_uom_qty
        if float_is_zero(total_qty, precision_rounding=move.product_uom.rounding):
            raise UserError(_("La quantità richiesta è 0, non posso creare colli."))

        if not self.quant_id:
            raise UserError(_("Seleziona 'Pick From' (Quant) per scegliere lotto/posizione."))

        qty_per_pkg = float(self.qty_per_package)
        if qty_per_pkg <= 0:
            raise UserError(_("La quantità per collo deve essere maggiore di 0."))

        # Calcolo numero colli + resto
        n_packages = int(total_qty // qty_per_pkg)
        remainder = total_qty - (n_packages * qty_per_pkg)

        # Se total_qty < qty_per_pkg -> faccio comunque 1 collo con total_qty
        if n_packages <= 0:
            n_packages = 1
            remainder = 0.0
            qty_first = total_qty
        else:
            qty_first = qty_per_pkg

        # 1) Cancello TUTTE le move lines esistenti di questo move
        # (solo se non done/cancel, ma qui ci siamo già protetti)
        move.move_line_ids.unlink()

        Package = self.env["stock.quant.package"]
        MoveLine = self.env["stock.move.line"]

        base_vals = {
            "picking_id": move.picking_id.id,
            "move_id": move.id,
            "product_id": move.product_id.id,
            "product_uom_id": move.product_uom.id,
            "location_dest_id": move.location_dest_id.id,
            "company_id": move.company_id.id,
            # dal quant selezionato:
            "location_id": self.quant_id.location_id.id,
            "lot_id": self.quant_id.lot_id.id,
            "package_id": self.quant_id.package_id.id,
        }

        # 2) Creo N colli + N righe
        for i in range(n_packages):
            pkg = Package.create({})
            qty = qty_first if i == 0 else qty_per_pkg
            MoveLine.create({
                **base_vals,
                "quantity": qty,
                "result_package_id": pkg.id,
            })

        # 3) Se c'è resto, creo un ultimo collo “parziale”
        # (così la somma torna SEMPRE alla richiesta)
        if float_compare(remainder, 0.0, precision_rounding=move.product_uom.rounding) > 0:
            pkg = Package.create({})
            MoveLine.create({
                **base_vals,
                "quantity": remainder,
                "result_package_id": pkg.id,
            })

        return {"type": "ir.actions.act_window_close"}
