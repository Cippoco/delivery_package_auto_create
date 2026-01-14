# -*- coding: utf-8 -*-
{
    "name": "delivery_package_auto_create",
    "version": "18.0.1.0.0",
    "author": "Brevatech / Riccardo Cipriani",
    "license": "OPL-1",
    "summary": "Patch per aggiungere automaticamente il numero colli in base alla quantità prodotti",
    "category": "Warehouse Management",
    "depends": ["stock"],
    "data": [
        "security/ir.model.access.csv",
        "views/auto_create_packages_wizard_views.xml",
        "views/stock_move_views.xml",
    ],
    "installable": True,
    "application": False,
}
