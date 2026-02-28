from odoo import fields, models


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    l10n_it_is_f24_payment = fields.Boolean(
        string="F24 Payment Ability",
        help="Indicates whether this bank account is used for F24 payments.",
    )
