# -*- coding: utf-8 -*-

from odoo import models


class StockMove(models.Model):
    _inherit = "stock.move"

    def action_open_delivery_auto_create_packages_wizard(self):
        """
        Apre il wizard 'delivery.auto.create.packages.wizard' forzando SEMPRE la view corretta,
        così in staging non può "pescare" una form view sbagliata (es. con campi sequence/company_id/state).
        """
        self.ensure_one()

        # Action base (quella definita in XML)
        action = self.env.ref(
            "delivery_package_auto_create.action_delivery_auto_create_packages_wizard"
        ).read()[0]

        # View form del wizard (quella definita in XML)
        wizard_view_id = self.env.ref(
            "delivery_package_auto_create.view_delivery_auto_create_packages_wizard_form"
        ).id

        # Forzo view_id + views per evitare che Odoo scelga una view "sporca" dal DB
        action.update({
            "name": action.get("name") or "Genera colli",
            "type": "ir.actions.act_window",
            "res_model": "delivery.auto.create.packages.wizard",
            "target": "new",
            "view_mode": "form",
            "view_id": wizard_view_id,
            "views": [(wizard_view_id, "form")],
            "context": dict(
                self.env.context,
                default_move_id=self.id,
                # opzionali ma utili
                default_company_id=self.company_id.id,
            ),
        })
        return action
