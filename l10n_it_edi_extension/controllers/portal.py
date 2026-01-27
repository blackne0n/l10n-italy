# Copyright 2025 Giuseppe Borruso - Dinamiche Aziendali srl
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo.http import Controller, request, route


class FatturaPAController(Controller):
    @route(
        [
            "/fatturapa/preview/<attachment_id>",
        ],
        type="http",
        auth="user",
        website=True,
    )
    def fatturapa_preview(self, attachment_id, **data):
        attach = request.env["ir.attachment"].browse(int(attachment_id))
        html = attach.get_fattura_elettronica_preview()
        if isinstance(html, bytes):
            html = html.decode()
        pdf = request.env["ir.actions.report"]._run_wkhtmltopdf(
            [html],
            specific_paperformat_args={
                "margin_top": 0,
                "margin_bottom": 0,
                "margin_left": 0,
                "margin_right": 0,
                "header_spacing": 0,
            },
        )
        pdfhttpheaders = [
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(pdf)),
            ("Content-Disposition", 'inline; filename="Fattura.pdf"'),
        ]
        return request.make_response(pdf, headers=pdfhttpheaders)
