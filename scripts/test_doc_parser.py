import sys
from app.ingestion.parser import parse_document

def test_doc_parsing():
    file_path = "tests/test_docs/附件：《开放式基金业务数据交换协议》.doc"
    try:
        doc = parse_document(file_path)
        print(f"Parsed Title: {doc.title}")
        print(f"Parsed Page Count: {doc.page_count}")
        print(f"Parsed Sections Count: {len(doc.sections)}")
        print(f"Parsed Tables Count: {len(doc.tables)}")
        
        # print first section
        if doc.sections:
            print("\nFirst Section:")
            print(f"Title: {doc.sections[0].title}")
            print(f"Level: {doc.sections[0].level}")
            print(f"Content (first 100 chars): {doc.sections[0].content[:100]}")
    except Exception as e:
        print(f"Error parsing document: {e}")

if __name__ == "__main__":
    test_doc_parsing()
