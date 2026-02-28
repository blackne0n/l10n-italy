from odoo import fields, models


class AccountTax(models.Model):
    _inherit = "account.tax"

    l10n_it_f24_codice_tributo_id = fields.Many2one(
        "l10n_it.f24.tributecode", string="F24 Code"
    )
