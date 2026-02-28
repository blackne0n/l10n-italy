import base64

from dateutil.relativedelta import relativedelta
from stdnum.it import codicefiscale

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_is_zero


class F24(models.Model):
    _name = "l10n_it.f24"
    _description = "F24"
    _order = "payment_date desc"
    _inherit = ["mail.thread"]

    name = fields.Char(
        string="Name",
        compute="_compute_name",
        store=True,
    )
    description = fields.Char(string="Description")
    protocol_number = fields.Char(string="Protocol Number")
    date = fields.Date(
        string="Date",
        required=True,
        default=fields.Date.context_today,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    amount = fields.Float(
        string="Total Amount",
        compute="_compute_amount",
        store=True,
    )
    payment_date = fields.Date(
        string="Payment Date",
        required=True,
        default=lambda self: (
            (
                lambda today: today + relativedelta(day=16, months=1)
                if today.day >= 16
                else today + relativedelta(day=16)
            )(fields.Date.context_today(self))
        ),
    )
    bank_account_id = fields.Many2one(
        "res.partner.bank",
        string="Bank Account",
        default=lambda self: self.env["res.partner.bank"].search(
            [
                ("partner_id", "=", self.env.company.partner_id.id),
                ("l10n_it_is_f24_payment", "=", True),
            ],
            limit=1,
        ),
    )

    attach_id = fields.Many2one("ir.attachment", string="F24 Attachment", readonly=True)

    create_move = fields.Boolean(string="Create Accounting Move", default=True)
    move_id = fields.Many2one("account.move", string="Related Move")

    residual = fields.Float(
        string="Residual Amount", compute="_compute_residual", store=True
    )
    reconciled = fields.Boolean(
        string="Reconciled", compute="_compute_residual", store=True
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("paid", "Paid"),
        ],
        string="Status",
        default="draft",
        required=True,
    )

    # Necessary if the fornitore is a person, otherwise ignored
    fornitore_birth_place = fields.Char(string="Fornitore Birth Place", length=25)
    fornitore_birth_province = fields.Char(string="Fornitore Birth Province", length=2)

    f24line_ids = fields.One2many("l10n_it.f24.line", "f24_id", string="F24 Lines")

    financial_office_code = fields.Char(string="Financial Office Code", length=3)
    act_code = fields.Char(string="ACT Code", length=11)

    section_totals_erario_debit = fields.Float(
        string="Erario Section Totals", compute="_compute_section_totals"
    )
    section_totals_erario_credit = fields.Float(
        string="Erario Section Totals", compute="_compute_section_totals"
    )
    section_totals_erario_indicator = fields.Char(
        string="Erario Section Totals", compute="_compute_section_totals"
    )
    section_totals_erario_amount = fields.Float(
        string="Erario Section Totals", compute="_compute_section_totals"
    )

    ## Computes
    @api.depends("f24line_ids.amount")
    def _compute_amount(self):
        for record in self:
            record.amount = sum(line.amount for line in record.f24line_ids)

    @api.depends(
        "f24line_ids.debit",
        "f24line_ids.credit",
        "f24line_ids.amount",
        "f24line_ids.section",
    )
    def _compute_section_totals(self):
        """Calculate the total amount for each section."""
        for record in self:
            section_totals = {
                "erario": [
                    0.0,
                    0.0,
                    " ",
                    0.0,
                ],  # [debit, credit, debit-credit (amount)]
                "inps": [0.0, 0.0, " ", 0.0],
                "regioni": [0.0, 0.0, " ", 0.0],
                "imu": [0.0, 0.0, " ", 0.0],
                "inail": [0.0, 0.0, " ", 0.0],
                "altro_previdence": [0.0, 0.0, " ", 0.0],
            }
            for line in record.f24line_ids:
                section_totals[line.section][0] += line.debit or 0.0
                section_totals[line.section][1] += line.credit or 0.0
                section_totals[line.section][3] += line.amount
            for section in section_totals:
                debit = section_totals[section][0]
                credit = section_totals[section][1]
                # If both d and c are 0.0, " ", if credit is more than debit, "N", if debit is more or equal than credit "P"
                if debit == 0.0 and credit == 0.0:
                    section_totals[section][2] = " "
                elif credit > debit:
                    section_totals[section][2] = "N"
                else:
                    section_totals[section][2] = "P"
            record.section_totals_erario_debit = section_totals["erario"][0]
            record.section_totals_erario_credit = section_totals["erario"][1]
            record.section_totals_erario_indicator = section_totals["erario"][2]
            record.section_totals_erario_amount = section_totals["erario"][3]

    @api.depends("date")
    def _compute_name(self):
        for record in self:
            record.name = (
                f"F24 - {record.date.strftime('%Y-%m-%d')}" if record.date else "F24"
            )

    def _checks(self):
        self.ensure_one()
        if self.amount < 0:
            raise ValidationError(_("The total amount of the F24 cannot be negative."))

    ## Actions
    def action_confirm(self):
        """Confirm the F24, changing its status to confirmed."""
        for record in self:
            if not record.f24line_ids:
                raise ValidationError(_("You cannot confirm an F24 without lines."))
            record._compute_section_totals()
            record._checks()
            record.action_create_move()
            record.action_print_f24()
            record.state = "confirmed"

    def action_create_move(self):
        """Create the accounting move for the F24."""
        for record in self:
            if not record.company_id.l10n_it_account_f24_journal_id:
                raise ValidationError(
                    _(
                        "The company must have an F24 journal to create the accounting move."
                    )
                )
            if not record.company_id.l10n_it_account_f24_clearing_account_id:
                raise ValidationError(
                    _(
                        "The company must have an F24 clearing account to create the accounting move."
                    )
                )
            if record.move_id:
                raise ValidationError(
                    _("An accounting move has already been created for this F24.")
                )
            move_obj = self.env["account.move"]
            move_vals = {
                "date": record.payment_date,
                "ref": record.name,
                "move_type": "entry",
                "company_id": record.company_id.id,
                "journal_id": record.company_id.l10n_it_account_f24_journal_id.id,
                "auto_post": "at_date",
            }
            move = move_obj.create(move_vals)
            move.line_ids = record._prepare_f24_move_lines()
            record.move_id = move.id

    def _prepare_f24_move_lines(self):
        self.ensure_one()
        currency = self.company_id.currency_id
        clearing = self.company_id.l10n_it_account_f24_clearing_account_id

        lines_to_create = []
        tot_debito = 0.0
        tot_credito = 0.0

        for line in self.f24line_ids:
            debito = line.debit or 0.0  # amount owed
            credito = line.credit or 0.0  # amount compensated

            if float_is_zero(
                debito, precision_rounding=currency.rounding
            ) and float_is_zero(credito, precision_rounding=currency.rounding):
                continue

            label = (
                f"{line.rateazione}/{line.ref_year} - {line.codice_tributo.code}"
                if line.rateazione and line.ref_year
                else f"{line.ref_year} - {line.codice_tributo.code}"
                if line.ref_year
                else line.codice_tributo.code
            )

            # Debito (owed): debit the liability account (reduces payable)
            if debito > 0:
                debit_account = (
                    line.debit_account_id or line.codice_tributo.debit_account_id
                )
                if not debit_account:
                    raise ValidationError(
                        _("Missing debit account for F24 line %s.") % (label or line.id)
                    )

                lines_to_create.append(
                    (
                        0,
                        0,
                        {
                            "name": label,
                            "account_id": debit_account.id,
                            "debit": debito,
                            "credit": 0.0,
                        },
                    )
                )
                tot_debito += debito

            # Credito (compensation): credit the tax credit asset account (reduces asset)
            if credito > 0:
                credit_account = (
                    line.credit_account_id or line.codice_tributo.credit_account_id
                )
                if not credit_account:
                    raise ValidationError(
                        _("Missing credit account for F24 line %s.")
                        % (label or line.id)
                    )

                lines_to_create.append(
                    (
                        0,
                        0,
                        {
                            "name": label,
                            "account_id": credit_account.id,
                            "debit": 0.0,
                            "credit": credito,
                        },
                    )
                )
                tot_credito += credito

        # Single balancing clearing line (net to pay)
        net = tot_debito - tot_credito
        if not float_is_zero(net, precision_rounding=currency.rounding):
            lines_to_create.append(
                (
                    0,
                    0,
                    {
                        "name": _("F24 clearing"),
                        "account_id": clearing.id,
                        "debit": 0.0 if net > 0 else -net,
                        "credit": net if net > 0 else 0.0,
                    },
                )
            )

        return lines_to_create

    @api.depends(
        "state", "move_id.line_ids.amount_residual", "move_id.line_ids.currency_id"
    )
    def _compute_residual(self):
        for line in self:
            precision = line.company_id.currency_id.decimal_places
            if not line.move_id:
                continue

            residual = 0.0
            for move_line in line.move_id.line_ids:
                clearing_account_id = (
                    line.company_id.l10n_it_account_f24_clearing_account_id.id
                )
                if move_line.account_id.id == clearing_account_id:
                    residual += move_line.amount_residual
            line.residual = abs(residual)
            if float_is_zero(line.residual, precision_digits=precision):
                line.reconciled = True
                line.state = "paid"
            else:
                line.reconciled = False
                line.state = "confirmed"

    def action_print_f24(self):
        """Print the F24 report."""
        self.ensure_one()
        self._compute_section_totals()
        newline = "\r\n"
        record = self._print_a_record()
        record += newline
        record += self._print_m_record()
        record += newline
        record += self._print_v_record()
        record += newline
        record += self._print_z_record()
        record += newline
        # IMPORTANT: F24 files are plain text; use ASCII (strict) to catch bad chars early
        record_bytes = record.encode("ascii", "strict")

        if self.attach_id:
            self.env["ir.attachment"].browse(self.attach_id.id).unlink()

        attachment = self.env["ir.attachment"].create(
            {
                "name": f"{self.name}.f24",
                "type": "binary",
                "datas": base64.b64encode(record_bytes),
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "text/plain",
            }
        )
        self.attach_id = attachment.id

        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    ## Print records
    def _print_a_record(self):
        "Print the header record."
        self.ensure_one()
        company = self.company_id.partner_id
        # Record type (1)
        record = "A"
        # Filler (2)
        record += " " * 14
        # Fornitura code (16)
        record += "F24A0"
        # Fornitore type (21) 14 for company, 04 for person
        record += "14" if company.is_company else "04"
        # Fornitore fiscal code (23)
        record += str(codicefiscale.compact(company.l10n_it_codice_fiscale)).ljust(16)[
            :16
        ]
        # If fornitore type is a company, skip person data and print company name, otherwise print person data
        if company.is_company:
            # Skip person data (117 chars)
            record += " " * 45
            record += "0" * 8
            record += " " * 119
            record += "0" * 5
            # Company name (216)
            record += str(company.name).ljust(60)[:60]
            record += " " * 77
            record += "0" * 5
            record += " " * 77
            record += "0" * 5
        else:
            # Fornitore is a person
            # Last name (39)
            name = str(company.firstname).ljust(24)[:24]
            surname = str(company.lastname).ljust(20)[:20]
            sex = codicefiscale.get_gender(company.l10n_it_codice_fiscale)
            birth_date = codicefiscale.get_birth_date(company.l10n_it_codice_fiscale)
            birth_place = str(self.fornitore_birth_place).ljust(40)[:40]
            birth_province = str(self.fornitore_birth_province).ljust(2)[:2]

            record += surname.ljust(24)[:24]
            record += name.ljust(20)[:20]
            record += sex
            record += birth_date.strftime("%d%m%Y")
            record += birth_place.ljust(40)[:40]
            record += birth_province
            # Skip address data
            record += " " * 82
            # Skip giuridical person data
            record += " " * 224

        # Origin flag
        record += " "
        # Reserved space
        record += " " * 81
        # Progressive
        record += "001"
        # Total n of send
        record += "001"
        # User fillable
        record += " " * 100
        # Reserved Space
        record += " " * 1270
        # Control character
        record += "A"

        return record

    def _print_m_record(self):
        self.ensure_one()
        company = self.company_id.partner_id
        # Record type (1)
        record = "M"
        # Fiscal code (2)
        record += str(codicefiscale.compact(company.l10n_it_codice_fiscale)).ljust(16)
        # Progressive (18)
        record += "00000001"
        # User space (26)
        record += " " * 3
        # Filler (29)
        record += " " * 25
        # User space, for F24 identification (54)
        record += " " * 20
        # Filler (74)
        record += " " * 16
        # Filler (90)
        record += " "
        # Valuta delega (always E) (91)
        record += "E"
        # Esercizio a cavallo (92)
        record += "0"
        # Flag versante/firmatario (93)
        record += "0"
        # Skip versante/firmatario data (x chars)
        record += " " * 16
        record += "0"
        record += " " * 45
        record += "0" * 8
        record += " " * 84
        record += "0" * 5
        record += " " * 35

        # Skip anagraphic residence of the taxpayer (288)
        record += " " * 42
        record += "0" * 5
        record += " " * 47
        # Filler
        record += " " * 56
        if not company.is_company:
            # last name (438)
            record += str(company.lastname).ljust(24)[:24]
            # first name (462)
            record += str(company.firstname).ljust(20)[:20]
            # Birth date (482)
            record += codicefiscale.get_birth_date(
                company.l10n_it_codice_fiscale
            ).strftime("%d%m%Y")
            # Sex (490)
            record += codicefiscale.get_gender(company.l10n_it_codice_fiscale)
            # Birth place (491)
            record += self.fornitore_birth_place.ljust(25)[:25]
            record += self.fornitore_birth_province.ljust(2)[:2]
        else:
            record += " " * 44
            record += "0" * 8
            record += " " * 28
            # Company name (518)
            record += str(company.name).ljust(55)[:55]
        # Skip coobbligato (573, 575)
        record += " " * 18
        # Filler (591)
        record += " " * 1150

        ## INFORMAZIONI CONTO DI ADDEBITO
        if self.amount > 0 and self.bank_account_id:
            # IBAN (1741)
            record += (self.bank_account_id.sanitized_acc_number).ljust(27)[:27]
        else:
            record += " " * 27
        # Filler (1768)
        record += " " * 41
        # Email (1809))
        record += " " * 60

        ## Dati di riepilogo della delega
        # Currency (Always EURO) (1869)
        record += "EURO"
        # total debt amount (1873)
        if self.amount > 0:
            record += (
                f"{self.amount:,.2f}".replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
                .ljust(15)
            )
        else:
            record += "0,00".ljust(15)
        # Versamento date (1888)
        record += self.payment_date.strftime("%d-%m-%Y")
        # Filler (1898)
        record += "A"

        return record

    def _print_v_record(self):
        self.ensure_one()
        record = self._print_v_record_header("A")
        # Only doing erario, so filter a maximum of 6 lines for erario
        record += self._print_v_record_erario(
            lines=self.f24line_ids.filtered(lambda l: l.section == "erario")
        )
        record += self._print_v_record_inps()
        record += self._print_v_record_regions()
        record += self._print_v_record_imu()
        record += self._print_v_record_inail()
        record += self._print_v_record_altro_previdence()
        # Filler (1743)
        record += " " * 50
        # Saldo Finale F24 (1793)
        record += f"{int(round((self.amount if self.amount >= 0 else 0) * 100)):015d}"
        # versamento date (1808)
        record += self.payment_date.strftime("%d%m%Y")
        # Filler (1816)
        record += " " * 82
        # Filler (1898)
        record += "A"

        return record

    def _print_z_record(self):
        self.ensure_one()
        # Record type (1)
        record = "Z"
        # Filler (2)
        record += " " * 14
        # Number of V records (16)
        record += "000000001"
        # Number of M records (25)
        record += "000000001"
        # Filler (34)
        record += " " * 1864
        # Filler (1898)
        record += "A"

        return record

    def _print_v_record_header(self, type, progressive=1):
        # Record type (1)
        record = "V"
        # Fiscal code (2)
        record += str(
            codicefiscale.compact(self.company_id.partner_id.l10n_it_codice_fiscale)
        ).ljust(16)[:16]
        # Progressive (18)
        record += str(progressive).zfill(8)
        # User space (26)
        record += " " * 3
        # Filler (29)
        record += " " * 25
        # User space, for F24 identification (54)
        record += " " * 20
        # Filler (74)
        record += " " * 16
        # Model Type (A for IMU) (90)
        record += str(type).ljust(1)[:1]
        return record

    def _print_v_record_erario(self, lines=None):
        record = ""
        if lines:
            # Financial office code (91)
            record += (
                str(self.financial_office_code).ljust(3)
                if self.financial_office_code
                else " " * 3
            )
            # Act code (94)
            record += str(self.act_code).zfill(11) if self.act_code else "0" * 11
            max_lines = 6
            line_record = ""
            for line in lines[:max_lines]:
                # Codice tributo (105)
                line_record += str(line.codice_tributo.code).ljust(4)[:4]
                # Codice certificazione (109)
                line_record += (
                    str(line.certification_number).ljust(16)[:16]
                    if line.certification_number
                    else " " * 16
                )
                # Rateazione/Regione/Provincia (125)
                line_record += str(line.rateazione or "").zfill(4)
                # Reference year (129)
                line_record += str(line.ref_year or "").zfill(4)
                # Debt amount (133)
                line_record += f"{int(round((line.debit or 0) * 100)):015d}"
                # Credit amount (148)
                line_record += f"{int(round((line.credit or 0) * 100)):015d}"
            missing = max_lines - min(len(lines), max_lines)
            line_record += ((" " * 24) + ("0" * 34)) * missing
            record += line_record
            # Section totals
            # Debt total (453)
            record += (
                f"{int(round((self.section_totals_erario_debit or 0) * 100)):015d}"
            )
            # Credit total
            record += (
                f"{int(round((self.section_totals_erario_credit or 0) * 100)):015d}"
            )
            # Debit/Credit indicator (P, N or space)
            record += self.section_totals_erario_indicator
            # Total amount
            record += (
                f"{int(round((self.section_totals_erario_amount or 0) * 100)):015d}"
            )
            return record

    def _print_v_record_inps(self):
        ## INPS section
        # Not doing it currently
        record = ""
        for i in range(4):
            record += "0" * 4
            record += " " * 21
            record += "0" * 42
        record += "0" * 30
        record += " "
        record += "0" * 15

        return record

    def _print_v_record_regions(self):
        ## Regions section
        # Not doing it currently
        record = ""
        for i in range(4):
            record += "0" * 2
            record += " " * 8
            record += "0" * 34
        record += "0" * 30
        record += " "
        record += "0" * 15

        return record

    def _print_v_record_imu(self):
        ## IMU section
        # Not doing it currently
        record = " " * 18
        for i in range(4):
            record += " " * 4
            record += "0" * 22
            record += " " * 8
            record += "0" * 34
        record += "0" * 30
        record += " "
        record += "0" * 15

        return record

    def _print_v_record_inail(self):
        ## INAIL section
        # Not doing it currently
        record = ""
        for i in range(3):
            record += "0" * 21
            record += " "
            record += "0" * 30
        record += "0" * 30
        record += " "
        record += "0" * 15

        return record

    def _print_v_record_altro_previdence(self):
        ## Altro Previdence section
        # Not doing it currently
        record = "0" * 4
        for i in range(2):
            record += " " * 9
            record += "0" * 51
        record += "0" * 30
        record += " "
        record += "0" * 15

        return record


class F24Line(models.Model):
    _name = "l10n_it.f24.line"
    _description = "F24 Line"

    f24_id = fields.Many2one(
        "l10n_it.f24", string="F24", required=True, ondelete="cascade"
    )
    section = fields.Selection(
        [
            ("erario", "Erario"),
            ("inps", "INPS"),
            ("regioni", "Regioni"),
            ("imu", "IMU e altri tributi locali"),
            ("inail", "INAIL"),
            ("altro_previdence", "Altri enti previdenziali e assistenziali"),
        ],
        string="Section",
        compute="_compute_section",
        store=True,
    )

    codice_tributo = fields.Many2one(
        string="Codice Tributo", required=True, comodel_name="l10n_it.f24.tributecode"
    )
    certification_number = fields.Char(string="Certification Number", length=16)
    rateazione = fields.Char(string="Rateazione/Regione/Provincia", length=4)
    ref_year = fields.Integer(string="Reference Year", length=4)
    debit = fields.Float(string="Debit Amount")
    credit = fields.Float(string="Credit Amount")

    debit_account_id = fields.Many2one("account.account", string="Debit Account")
    credit_account_id = fields.Many2one("account.account", string="Credit Account")

    amount = fields.Float(string="Amount", compute="_compute_amount", store=True)

    ## Computes
    @api.depends("debit", "credit")
    def _compute_amount(self):
        for record in self:
            if record.debit and record.credit:
                raise ValidationError(
                    _("A line cannot have both debit and credit amounts.")
                )
            elif record.debit:
                record.amount = record.debit
            elif record.credit:
                record.amount = -record.credit

    @api.depends("codice_tributo")
    def _compute_section(self):
        for record in self:
            record.section = record.codice_tributo.section

    @api.depends("codice_tributo")
    def _compute_ratezione(self):
        for record in self:
            if record.codice_tributo.rateazione in ("FFFF", "0000"):
                record.rateazione = "0000"

    ## Checks
    @api.depends("codice_tributo")
    def _check_der(self):
        for record in self:
            if record.codice_tributo.usage_mode == "d" and not record.debit:
                raise ValidationError(
                    _("This codice tributo can only be used with debit amounts.")
                )
            if record.codice_tributo.usage_mode == "e" and not record.credit:
                raise ValidationError(
                    _("This codice tributo can only be used with credit amounts.")
                )

    @api.constrains("codice_tributo", "debit", "credit")
    def _check_der_constrains(self):
        self._check_der()

    @api.depends("rateazione", "codice_tributo")
    def _check_rateazione(self):
        for record in self:
            if (
                record.codice_tributo.rateazione not in ("FFFF", "0000")
                and not record.rateazione
            ):
                raise ValidationError(
                    _("Rateazione is required for this codice tributo.")
                )
            if (
                record.rateazione
                and len(record.rateazione) != 4
                and record.codice_tributo.rateazione not in ("FFFF", "0000")
            ):
                raise ValidationError(_("Rateazione must be 4 characters long."))


class F24TributeCode(models.Model):
    _name = "l10n_it.f24.tributecode"
    _description = "F24 Tribute Code"
    _rec_name = "code"

    code = fields.Char(string="Code", required=True, length=4)
    description = fields.Char(string="Description", required=True)
    active = fields.Boolean(string="Active", default=True)
    usage_mode = fields.Selection(
        selection=[
            ("d", "Debit only"),
            ("e", "Credit only"),
            ("r", "Any"),
        ],
        required=True,
    )
    section = fields.Selection(
        selection=[
            ("erario", "Erario"),
            ("inps", "INPS"),
            ("regioni", "Regioni"),
            ("imu", "IMU e altri tributi locali"),
            ("inail", "INAIL"),
            ("altro_previdence", "Altri enti previdenziali e assistenziali"),
        ],
        string="Section",
        required=True,
    )
    rateazione = fields.Selection(
        string="Rateazione/Regione/Provincia",
        selection=[
            ("0000", "0000"),
            ("00MM", "Mese"),
            ("00T0", "Regioni"),
            ("00T1", "Enti Locali"),
            ("00T2", "Province italiane"),
            ("00T3", "Enti territoriali emittenti prestiti obbligazionari"),
            ("00T4", "Codici catastali dei comuni italiani"),
            ("FFFF", "07"),
            ("NNRR", "Rateazione"),
        ],
        required=True,
    )
    ref_year = fields.Selection(
        string="Reference Year",
        selection=[
            ("0000", "None"),
            ("AAAA", "Year"),
        ],
        required=True,
    )
    office_code = fields.Boolean(string="Office code required")
    act_code = fields.Boolean(string="ACT code required")
    max_ref_year = fields.Integer(string="Max reference year", default=0)

    debit_account_id = fields.Many2one(
        "account.account", string="Default Debit Account"
    )
    credit_account_id = fields.Many2one(
        "account.account", string="Default Credit Account"
    )

    @api.depends("code", "description")
    def _compute_name(self):
        for record in self:
            record.name = f"{record.code} - {record.description}"
