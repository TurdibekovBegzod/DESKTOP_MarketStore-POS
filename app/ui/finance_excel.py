import html
import zipfile
from datetime import date


def _col_name(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell_ref(row, column):
    return f"{_col_name(column)}{row}"


def _sheet_name(name):
    return name.replace("'", "''")


def _xml(value):
    return html.escape(str(value), quote=True)


def _number_cell(ref, value, style=0):
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}"{style_attr}><v>{float(value or 0):.2f}</v></c>'


def _text_cell(ref, value, style=0):
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{_xml(value)}</t></is></c>'


def _formula_cell(ref, formula, display, style=0):
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}" t="str"{style_attr}><f>{_xml(formula)}</f><v>{_xml(display)}</v></c>'


def _worksheet_xml(rows, formulas=None, chart=None):
    formulas = formulas or {}
    sheet_rows = []
    max_col = 1
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            max_col = max(max_col, col_index)
            ref = _cell_ref(row_index, col_index)
            if ref in formulas:
                formula, display, style = formulas[ref]
                cells.append(_formula_cell(ref, formula, display, style))
            elif isinstance(value, (int, float)):
                cells.append(_number_cell(ref, value, 3 if row_index > 1 else 2))
            else:
                style = 2 if row_index == 1 else 0
                cells.append(_text_cell(ref, value, style))
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    drawing = '<drawing r:id="rId1"/>' if chart else ""
    dimension = f'A1:{_cell_ref(max(len(rows), 1), max_col)}'
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="{dimension}"/>
  <sheetViews><sheetView workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="15"/>
  <sheetData>{"".join(sheet_rows)}</sheetData>
  {drawing}
</worksheet>'''


def _sheet_rels_xml(chart_id):
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing{chart_id}.xml"/>
</Relationships>'''


def _drawing_xml(chart_id):
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <xdr:twoCellAnchor>
    <xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>3</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
    <xdr:to><xdr:col>8</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>20</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
    <xdr:graphicFrame macro="">
      <xdr:nvGraphicFramePr><xdr:cNvPr id="2" name="Chart {chart_id}"/><xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>
      <xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>
      <a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rId1"/></a:graphicData></a:graphic>
    </xdr:graphicFrame>
    <xdr:clientData/>
  </xdr:twoCellAnchor>
</xdr:wsDr>'''


def _drawing_rels_xml(chart_id):
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart{chart_id}.xml"/>
</Relationships>'''


def _chart_xml(sheet_name, title, category_range, value_range):
    sheet = _sheet_name(sheet_name)
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <c:chart>
    <c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{_xml(title)}</a:t></a:r></a:p></c:rich></c:tx><c:layout/></c:title>
    <c:plotArea>
      <c:layout/>
      <c:lineChart>
        <c:grouping val="standard"/>
        <c:ser>
          <c:idx val="0"/><c:order val="0"/>
          <c:tx><c:strRef><c:f>'{sheet}'!$B$1</c:f></c:strRef></c:tx>
          <c:cat><c:strRef><c:f>'{sheet}'!{category_range}</c:f></c:strRef></c:cat>
          <c:val><c:numRef><c:f>'{sheet}'!{value_range}</c:f></c:numRef></c:val>
          <c:smooth val="0"/>
        </c:ser>
        <c:axId val="123456"/><c:axId val="123457"/>
      </c:lineChart>
      <c:catAx><c:axId val="123456"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:axPos val="b"/><c:tickLblPos val="nextTo"/><c:crossAx val="123457"/></c:catAx>
      <c:valAx><c:axId val="123457"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:axPos val="l"/><c:majorGridlines/><c:numFmt formatCode="#,##0.00" sourceLinked="0"/><c:tickLblPos val="nextTo"/><c:crossAx val="123456"/></c:valAx>
    </c:plotArea>
    <c:legend><c:legendPos val="b"/><c:layout/></c:legend>
    <c:plotVisOnly val="1"/>
  </c:chart>
</c:chartSpace>'''


def export_finance_xlsx(path, summary_rows, monthly_rows, yearly_rows, currency_code):
    monthly_end = max(2, len(monthly_rows))
    yearly_end = max(2, len(yearly_rows))
    sheets = [
        ("Finance", summary_rows, None),
        ("Oylik", monthly_rows, {"title": f"Oylik summa ({currency_code})", "categories": f"$A$2:$A${monthly_end}", "values": f"$B$2:$B${monthly_end}"}),
        ("Yillik", yearly_rows, {"title": f"Yillik summa ({currency_code})", "categories": f"$A$2:$A${yearly_end}", "values": f"$B$2:$B${yearly_end}"}),
    ]

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml(len(sheets), 2))
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("xl/workbook.xml", _workbook_xml([name for name, _, _ in sheets]))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(len(sheets)))
        archive.writestr("xl/styles.xml", _styles_xml())

        chart_id = 1
        for index, (name, rows, chart) in enumerate(sheets, start=1):
            formulas = {}
            if name == "Finance":
                formulas = {
                    "A1": ("HYPERLINK(\"#'Oylik'!A1\",\"Oylik\")", "Oylik", 4),
                    "B1": ("HYPERLINK(\"#'Yillik'!A1\",\"Yillik\")", "Yillik", 4),
                }
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows, formulas=formulas, chart=chart))
            if chart:
                archive.writestr(f"xl/worksheets/_rels/sheet{index}.xml.rels", _sheet_rels_xml(chart_id))
                archive.writestr(f"xl/drawings/drawing{chart_id}.xml", _drawing_xml(chart_id))
                archive.writestr(f"xl/drawings/_rels/drawing{chart_id}.xml.rels", _drawing_rels_xml(chart_id))
                archive.writestr(f"xl/charts/chart{chart_id}.xml", _chart_xml(name, chart["title"], chart["categories"], chart["values"]))
                chart_id += 1


def _content_types_xml(sheet_count, chart_count):
    sheets = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    charts = "".join(
        f'<Override PartName="/xl/charts/chart{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        f'<Override PartName="/xl/drawings/drawing{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
        for index in range(1, chart_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  {sheets}{charts}
</Types>'''


def _root_rels_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''


def _workbook_xml(sheet_names):
    sheets = "".join(
        f'<sheet name="{_xml(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{sheets}</sheets>
</workbook>'''


def _workbook_rels_xml(sheet_count):
    rels = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    rels += f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>'''


def _styles_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
  <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2563EB"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="5">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
    <xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
