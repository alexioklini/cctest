---
name: write_document_tool
description: Core document generation backend that the Reporter depends on for PDF/DOCX/PPTX/XLSX output
type: reference
agent: main
last_recalled: 2026-04-08
---

Tool responsible for creating richly formatted documents from markdown content. Supports automatic format selection based on file extension: .docx uses headings/tables/bold, .xlsx converts markdown tables to sheets, .pptx converts # sections to slides, .pdf uses reportlab for basic formatting. This backend tool is essential for the Reporter's document rendering capabilities.
