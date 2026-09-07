import unittest
from unittest.mock import patch
import io
import pymupdf
from PIL import Image
from invoice_verifier.ocr.amount_reader import select_total, spatial_text


class AmountReaderTests(unittest.TestCase):
    def test_vendor_totals(self):
        samples = [
            ("Barakat Quality Plus\nTotal net amount: 313.05\nTotal VAT: 15.68\nTotal amount due: 328.73", "328.73"),
            ("ABU DHABI REFRESHMENTS CO.L.L.C\nTOTAL SALES AMOUNT 52.08\nTOTAL NET AMOUNT 50.17", "50.17"),
            ("DUBAI REFRESHMENT PJSC\nNET VALUE BEFORE VAT 89.97\nTOTAL/GROSS(AED) 94.47", "94.47"),
            ("Mohebi Logistics\nTotal Dirhams One Thousand Five Hundred Sixty Nine and Fils\nTotal Dirhams 10.000 1,495.16 74.75 1,569.91", "1569.91"),
            ("M.H. ENTERPRISES L.L.C.\nTotal Amount 0.00 641.50 0.00 641.50 32.08 673.58", "673.58"),
        ]
        for text, amount in samples:
            with self.subTest(amount=amount):
                self.assertEqual(amount, select_total(text)['amount_total'])

    def test_scanned_pdf_does_not_trust_hidden_text(self):
        from invoice_verifier.ocr.amount_reader import extract_amount
        document = pymupdf.open()
        page = document.new_page(width=600, height=800)
        stream = io.BytesIO()
        Image.new('RGB', (60, 80), 'white').save(stream, format='PNG')
        page.insert_image(page.rect, stream=stream.getvalue())
        page.insert_text((30, 30), 'Grand Total 1102.00')
        raw = document.tobytes()
        document.close()
        with patch('invoice_verifier.ocr.amount_reader.read_summary', return_value=None), patch('invoice_verifier.ocr.amount_reader.read_image', return_value={
                'amount_total':'395.45', 'vendor_name':'Barakat Quality Plus'}) as scan:
            result = extract_amount(raw, 'scan.pdf')
        self.assertEqual('395.45', result['amount_total'])
        scan.assert_called_once()

    def test_conflicting_page_totals_are_not_approved(self):
        from invoice_verifier.ocr.amount_reader import extract_amount
        document=pymupdf.open()
        for value in ('100.00', '200.00'):
            page=document.new_page()
            page.insert_text((30, 30), 'Grand Total '+value)
        raw=document.tobytes()
        document.close()
        result=extract_amount(raw, 'two-invoices.pdf')
        self.assertIsNone(result['amount_total'])
        self.assertIn('Conflicting', result['amount_evidence'])

    def test_large_pdf_raster_size_is_bounded(self):
        from invoice_verifier.ocr.amount_reader import extract_amount
        document=pymupdf.open()
        document.new_page(width=3000,height=4000)
        raw=document.tobytes()
        document.close()
        sizes=[]
        def read(image):
            sizes.append(image.size)
            return {'amount_total':None, 'vendor_name':'Unknown'}
        with patch('invoice_verifier.ocr.amount_reader.read_summary', return_value=None), patch('invoice_verifier.ocr.amount_reader.read_image', side_effect=read):
            extract_amount(raw, 'oversized.pdf')
        self.assertLessEqual(max(sizes[0]),2600)

    def test_active_service_routes_to_new_reader_without_excel_input(self):
        import base64
        from invoice_verifier.ocr.ocr_service import extract_document
        with patch('invoice_verifier.ocr.amount_reader.extract_amount', return_value={'amount_total':'105.00'}) as read:
            result = extract_document(base64.b64encode(b'invoice bytes'), 'bill.pdf', 'invoice', amount_only=True)
        read.assert_called_once_with(b'invoice bytes', 'bill.pdf')
        self.assertEqual('105.00', result['amount_total'])

    def test_mh_table_does_not_select_a_unit_price(self):
        text='M.H. ENTERPRISES\nVehicle No. Total Amount 104.75 123.50 289.75'
        self.assertIsNone(select_total(text)['amount_total'])

    def test_abu_dhabi_stamp_over_total_label(self):
        text='ABU DHABI REFRESHMENTS\nTOTAL SALES AMOUNT 697.94\nTOTAL VAT AMOUNT 32.59\nKPdTAed NETinAMOUNT 684.47'
        self.assertEqual('684.47', select_total(text)['amount_total'])

    def test_barakat_second_page_final_total(self):
        from invoice_verifier.ocr.amount_reader import extract_amount
        document=pymupdf.open()
        first=document.new_page()
        first.insert_text((30,30),'Barakat Quality Plus')
        first.insert_text((30,60),'Total net amount: 313.05')
        second=document.new_page()
        second.insert_text((30,30),'Barakat Quality Plus')
        second.insert_text((30,60),'Total amount due: 328.73')
        raw=document.tobytes()
        document.close()
        with patch('invoice_verifier.ocr.amount_reader.read_summary',return_value=None):
            result=extract_amount(raw,'barakat.pdf')
        self.assertEqual('328.73',result['amount_total'])

    def test_label_letter_errors_do_not_change_amount_digits(self):
        text='Barakat\nTotal net amount 178.98\nTolal arnount due: 187.94'
        self.assertEqual('187.94',select_total(text)['amount_total'])

    def test_conflicting_ocr_digits_are_rejected_not_replaced(self):
        from invoice_verifier.ocr.amount_reader import validate_result
        result={'amount_total':'488.56', 'vendor_name':'Barakat Quality Plus',
                'raw_text':'Total amount due: 488.56\nAED**Four Hundred Eighty Six and 56/100'}
        checked=validate_result(result)
        self.assertIsNone(checked['amount_total'])
        self.assertEqual('488.56',result['amount_total'])
        self.assertIn('conflict',checked['amount_evidence'])

    def test_vat_inclusive_abu_dhabi_net_is_not_treated_as_subtotal(self):
        from invoice_verifier.ocr.amount_reader import validate_result
        result={'amount_total':'50.17','vendor_name':'Abu Dhabi Refreshments',
                'raw_text':'TOTAL NET AMOUNT 50.17\nTOTAL VAT AMOUNT 2.39'}
        self.assertEqual('50.17', validate_result(result)['amount_total'])

    def test_subtotal_and_vat_do_not_change_a_read_final_amount(self):
        from invoice_verifier.ocr.amount_reader import validate_result
        result={'amount_total':'488.56','vendor_name':'Barakat Quality Plus',
                'raw_text':'Total net amount 463.38\nTotal VAT 23.18\nTotal amount due 488.56'}
        self.assertEqual('488.56', validate_result(result)['amount_total'])

    def test_subtotal_is_never_payable(self):
        for text in ["Subtotal 100.00\nVAT 5.00", "Total net amount: 100.00\nTotal VAT: 5.00"]:
            self.assertIsNone(select_total(text)['amount_total'])

    def test_conflicting_totals_decline(self):
        self.assertIsNone(select_total('Grand Total 100.00\nAmount payable 105.00')['amount_total'])

    def test_no_decimal_truncation(self):
        self.assertIsNone(select_total('Grand total 105.001')['amount_total'])

    def test_negative_amount_is_not_turned_positive(self):
        self.assertIsNone(select_total('Grand Total -105.00')['amount_total'])

    def test_same_row_join_preserves_amount(self):
        def box(x, y, text):
            return [[[x,y],[x+100,y],[x+100,y+20],[x,y+20]],text,.99]
        text = spatial_text([box(400,52,'105.00'), box(10,50,'Total amount due:'), box(10,10,'Vendor')])
        self.assertEqual('105.00', select_total(text)['amount_total'])

    def test_other_row_amount_is_not_attached_to_missing_total(self):
        def box(y, text):
            return [[[0,y],[100,y],[100,y+10],[0,y+10]],text,.99]
        text=spatial_text([box(10,'Total amount due:'),box(40,'125.00')])
        self.assertIsNone(select_total(text)['amount_total'])

    @staticmethod
    def dubai_skewed_summary():
        # OCR geometry from INV99897709: right-column amounts sit above labels.
        data = [
            (66,237,319,224,320,253,68,266,'TOTALVALUEBEFORETAX'),
            (70,268,183,265,184,289,70,291,'EXCISETAX'),
            (69,306,346,293,348,322,70,335,'NETVALUEBEFOREVAT(AED)'),
            (69,353,166,353,166,380,69,380,'VAT(AED)'),
            (73,401,258,394,259,421,74,428,'TOTAL/GROSS(AED)'),
            (502,211,564,206,566,231,504,236,'73.80'),
            (513,240,564,236,566,263,515,267,'0.00'),
            (502,270,566,265,568,293,504,298,'73.80'),
            (514,321,566,318,568,343,516,345,'3.69'),
            (504,370,566,368,567,392,505,395,'77.49'),
            (0,524,433,510,434,537,0,551,'OFFICIAL DUBAI REFRESHMENT PJSC RECEIPT'),
        ]
        return [[[[r[i],r[i+1]] for i in range(0,8,2)],r[8],.99] for r in data]

    def test_dubai_skewed_receipt_final_total(self):
        result = select_total(spatial_text(self.dubai_skewed_summary()))
        self.assertEqual('77.49', result['amount_total'])
        self.assertEqual('Dubai Refreshment', result['vendor_name'])

    def test_dubai_missing_final_digits_does_not_use_vat(self):
        boxes = [b for b in self.dubai_skewed_summary() if b[1] != '77.49']
        self.assertIsNone(select_total(spatial_text(boxes))['amount_total'])

    def test_dubai_incomplete_summary_is_not_realigned(self):
        boxes = [b for b in self.dubai_skewed_summary() if b[1] != 'EXCISETAX']
        self.assertIsNone(select_total(spatial_text(boxes))['amount_total'])

    def test_dubai_extra_amount_is_ambiguous(self):
        boxes = self.dubai_skewed_summary()
        boxes.append([[[502,340],[566,340],[566,360],[502,360]],'88.88',.99])
        self.assertIsNone(select_total(spatial_text(boxes))['amount_total'])

    def test_dubai_realignment_requires_vendor(self):
        boxes = self.dubai_skewed_summary()[:-1]
        self.assertIsNone(select_total(spatial_text(boxes))['amount_total'])
