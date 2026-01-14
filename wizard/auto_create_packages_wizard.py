# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_is_zero


class DeliveryAutoCreatePackagesWizard(models.TransientModel):
    _name = "delivery.auto.create.packages.wizard"
    _description = "Assign move quantities to quants/lots and put everything in ONE existing destination package"

    sequence = fields.Integer(default=10)
    # company_id = fields.Many2one(
    #     "res.company",
    #     related="move_id.company_id",
    #     readonly=True,
    # )
    company_id = fields.Many2one(
        'res.company', 'Company',
        default=lambda self: self.env.company,
        index=True, required=True)

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

    # Campo tecnico per domain del quant (non lo mostriamo in view)
    product_id = fields.Many2one(
        comodel_name="product.product",
        related="move_id.product_id",
        readonly=True,
        store=False,
    )

    # Selezionabili (come richiesto)
    location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Preleva da",
        required=True,
    )
    location_dest_id = fields.Many2one(
        comodel_name="stock.location",
        string="Stocca in",
        required=True,
        domain="[('usage', '!=', 'view')]",
    )

    qty_per_package = fields.Float(
        string="Q.tà per riga",
        default=1.0,
        required=True,
    )

    quant_id = fields.Many2one(
        comodel_name="stock.quant",
        string="Pick From (lotto/ubicazione)",
        required=True,
    )

    # Un SOLO collo di destinazione, ESISTENTE
    result_package_id = fields.Many2one(
        comodel_name="stock.quant.package",
        string="Collo di destinazione",
        required=True,
        domain="['|', ('location_id', '=', False), ('location_id', '=', location_dest_id)]",
    )

    @api.constrains("qty_per_package")
    def _check_qty_per_package(self):
        for w in self:
            if w.qty_per_package <= 0:
                raise UserError(_("La quantità deve essere maggiore di 0."))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        move_id = res.get("move_id") or self.env.context.get("default_move_id")
        if not move_id:
            return res

        move = self.env["stock.move"].browse(move_id)

        if "move_id" in fields_list:
            res["move_id"] = move_id

        if "location_id" in fields_list and not res.get("location_id"):
            res["location_id"] = move.location_id.id

        if "location_dest_id" in fields_list and not res.get("location_dest_id"):
            res["location_dest_id"] = move.location_dest_id.id

        # Default quant: primo disponibile FIFO nella location del move
        if "quant_id" in fields_list and not res.get("quant_id"):
            quant = self.env["stock.quant"].sudo().search(
                [
                    ("product_id", "=", move.product_id.id),
                    ("location_id", "child_of", move.location_id.id),
                    ("quantity", ">", 0),
                ],
                order="in_date asc, id asc",
                limit=1,
            )
            if quant:
                res["quant_id"] = quant.id

        return res

    @api.onchange("quant_id")
    def _onchange_quant_id(self):
        if self.quant_id and self.quant_id.location_id:
            self.location_id = self.quant_id.location_id

    @api.onchange("location_id")
    def _onchange_location_id(self):
        # Se cambio location e il quant non è dentro la subtree, lo resetto
        if self.location_id and self.quant_id:
            ok = self.env["stock.location"].search_count([
                ("id", "=", self.quant_id.location_id.id),
                ("id", "child_of", self.location_id.id),
            ])
            if not ok:
                self.quant_id = False

    def _get_quants_fifo(self, product, location):
        return self.env["stock.quant"].sudo().search(
            [
                ("product_id", "=", product.id),
                ("location_id", "child_of", location.id),
                ("quantity", ">", 0),
            ],
            order="in_date asc, id asc",
        )

    def action_generate_packages(self):
        self.ensure_one()
        move = self.move_id

        if not move:
            raise UserError(_("Move non trovato."))
        if move.state in ("done", "cancel"):
            raise UserError(_("Non puoi rigenerare righe su un movimento Done/Cancelled."))

        total_qty = move.product_uom_qty
        if float_is_zero(total_qty, precision_rounding=move.product_uom.rounding):
            raise UserError(_("La quantità richiesta è 0, non posso generare righe."))

        if not self.result_package_id:
            raise UserError(_("Seleziona un Collo di destinazione esistente."))

        qty_step = float(self.qty_per_package)
        if qty_step <= 0:
            raise UserError(_("La quantità deve essere maggiore di 0."))

        # 1) Cancello tutte le move lines esistenti
        move.move_line_ids.unlink()

        MoveLine = self.env["stock.move.line"]

        base_vals = {
            "picking_id": move.picking_id.id,
            "move_id": move.id,
            "product_id": move.product_id.id,
            "product_uom_id": move.product_uom.id,
            "location_dest_id": self.location_dest_id.id,
            "company_id": move.company_id.id,
            "result_package_id": self.result_package_id.id,  # tutto nello stesso collo
        }

        # 2) prendo quants FIFO nella location selezionata
        quants = self._get_quants_fifo(move.product_id, self.location_id)
        if not quants:
            raise UserError(_("Nessun lotto/quant disponibile nella location selezionata."))

        # parto dal quant scelto, poi FIFO sugli altri
        ordered_quants = self.quant_id + (quants - self.quant_id)

        remaining = total_qty
        qi = 0

        def _create_ml(qty, quant):
            vals = dict(base_vals)
            vals.update({
                "quantity": qty,
                "location_id": quant.location_id.id,
                "lot_id": quant.lot_id.id,
                "package_id": quant.package_id.id,
            })
            MoveLine.create(vals)

        while float_compare(remaining, 0.0, precision_rounding=move.product_uom.rounding) > 0:
            if qi >= len(ordered_quants):
                raise UserError(_("Quantità insufficiente sui lotti disponibili: manca ancora %s.") % remaining)

            quant = ordered_quants[qi]
            avail = quant.available_quantity  # ok in python

            if float_compare(avail, 0.0, precision_rounding=move.product_uom.rounding) <= 0:
                qi += 1
                continue

            take = min(qty_step, remaining, avail)
            _create_ml(take, quant)
            remaining -= take

            # se esaurito, passa al prossimo
            if float_compare(avail - take, 0.0, precision_rounding=move.product_uom.rounding) <= 0:
                qi += 1

        return {"type": "ir.actions.act_window_close"}
