# -*- coding: utf-8 -*-
from odoo import api, models


class StockMove(models.Model):
    _inherit = "stock.move"

    def action_open_delivery_auto_create_packages_wizard(self):
        self.ensure_one()
        action = self.env.ref(
            "delivery_package_auto_create.action_delivery_auto_create_packages_wizard"
        ).read()[0]
        action["context"] = {
            **self.env.context,
            "default_move_id": self.id,
        }
        return action
