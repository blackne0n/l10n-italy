from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_it_account_f24_journal_id = fields.Many2one(
        "account.journal",
        string="F24 Journal",
        domain="[('company_id', '=', id), ('type', '=', 'general')]",
    )

    l10n_it_account_f24_clearing_account_id = fields.Many2one(
        "account.account",
        string="F24 Clearing Account",
    )
