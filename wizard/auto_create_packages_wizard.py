# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_is_zero


class DeliveryAutoCreatePackagesWizard(models.TransientModel):
    _name = "delivery.auto.create.packages.wizard"
    _description = "Auto create move lines by qty per package and put everything in one existing destination package"

    move_id = fields.Many2one(
        comodel_name="stock.move",
        string="Move",
        required=True,
        readonly=True,
    )

    # Related utili (per view e domini)
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

    qty_per_package = fields.Float(string="Q.tà per collo", default=1.0, required=True)

    # IMPORTANT: si seleziona un collo ESISTENTE, non si crea
    result_package_id = fields.Many2one(
        comodel_name="stock.quant.package",
        string="Collo di destinazione",
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
        return res

    def _get_available_quants(self, move):
        """Ritorna i quants in location sorgente con qty disponibile (>0) per il prodotto del move.
        Niente available_quantity (non domainabile). Usiamo quantity - reserved_quantity.
        """
        Quant = self.env["stock.quant"].sudo()
        return Quant.search([
            ("product_id", "=", move.product_id.id),
            ("location_id", "child_of", move.location_id.id),
            ("quantity", ">", 0),
        ], order="lot_id asc, id asc")

    def action_generate_packages(self):
        self.ensure_one()

        move = self.move_id
        if not move:
            raise UserError(_("Move non trovato."))

        if move.state in ("done", "cancel"):
            raise UserError(_("Non puoi rigenerare colli su un movimento Done/Cancelled."))

        if not self.result_package_id:
            raise UserError(_("Seleziona un collo di destinazione (esistente)."))

        total_qty = move.product_uom_qty  # richiesto
        if float_is_zero(total_qty, precision_rounding=move.product_uom.rounding):
            raise UserError(_("La quantità richiesta è 0, non posso generare righe."))

        qty_per_pkg = float(self.qty_per_package)
        if qty_per_pkg <= 0:
            raise UserError(_("La quantità per collo deve essere maggiore di 0."))

        # 1) cancello tutte le righe esistenti
        move.move_line_ids.unlink()

        quants = self._get_available_quants(move)
        if not quants:
            raise UserError(_("Nessun lotto/quant disponibile nella location sorgente per questo prodotto."))

        # 2) creo righe spezzando per qty_per_package e consumando i quants/lotti in ordine
        MoveLine = self.env["stock.move.line"]
        remaining = total_qty

        base_vals_common = {
            "picking_id": move.picking_id.id,
            "move_id": move.id,
            "product_id": move.product_id.id,
            "product_uom_id": move.product_uom.id,
            "location_dest_id": move.location_dest_id.id,
            "company_id": move.company_id.id,
            "result_package_id": self.result_package_id.id,  # TUTTO nello stesso collo
        }

        # somma disponibilità totale (per dare errore pulito se non basta)
        avail_total = 0.0
        for q in quants:
            q_available = q.quantity - q.reserved_quantity
            if q_available <= 0:
                continue
            # convertiamo la quantità del quant (in UoM prodotto) nella UoM del move
            avail_total += move.product_uom._compute_quantity(q_available, move.product_uom)

        if float_compare(avail_total, total_qty, precision_rounding=move.product_uom.rounding) < 0:
            raise UserError(_(
                "Quantità disponibile insufficiente.\n"
                "Richiesta: %(req)s\nDisponibile: %(avail)s"
            ) % {"req": total_qty, "avail": avail_total})

        for q in quants:
            if float_compare(remaining, 0.0, precision_rounding=move.product_uom.rounding) <= 0:
                break

            q_available = q.quantity - q.reserved_quantity
            if q_available <= 0:
                continue

            # disponibilità quant convertita nella uom del move
            q_avail_move_uom = move.product_uom._compute_quantity(q_available, move.product_uom)
            if float_compare(q_avail_move_uom, 0.0, precision_rounding=move.product_uom.rounding) <= 0:
                continue

            # finché ho disponibilità su questo quant e mi manca da soddisfare, creo righe
            while (
                float_compare(remaining, 0.0, precision_rounding=move.product_uom.rounding) > 0
                and float_compare(q_avail_move_uom, 0.0, precision_rounding=move.product_uom.rounding) > 0
            ):
                qty_line = min(qty_per_pkg, remaining, q_avail_move_uom)

                MoveLine.create({
                    **base_vals_common,
                    "location_id": q.location_id.id,
                    "lot_id": q.lot_id.id,
                    "package_id": q.package_id.id,
                    "quantity": qty_line,
                })

                remaining -= qty_line
                q_avail_move_uom -= qty_line

        # sicurezza finale
        if float_compare(remaining, 0.0, precision_rounding=move.product_uom.rounding) > 0:
            raise UserError(_("Non sono riuscito a coprire tutta la quantità richiesta. Rimanente: %s") % remaining)

        return {"type": "ir.actions.act_window_close"}
