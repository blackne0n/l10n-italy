# Copyright 2025 Alberto Giardino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

{
    "name": "Italy - F24 - Base",
    "version": "18.0.0.0.0",
    "category": "Accounting/Localizations/EDI",
    "development_status": "Alpha",
    "summary": "F24 base feature",
    "author": "Alberto Giardino, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-italy",
    "license": "AGPL-3",
    "external_dependencies": {
        "python": [
            "codicefiscale",
            "openupgradelib",
        ],
    },
    "depends": [
        "account",
        "l10n_it_edi",
        "partner_firstname",
    ],
    "data": [
        "data/f24_tribute_codes.xml",
        "views/f24.xml",
    ],
    "installable": True,
}
