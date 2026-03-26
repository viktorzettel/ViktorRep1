
import sys

def try_pdfplumber(pdf_path):
    print("--- Trying pdfplumber ---")
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            print(f"pdfplumber found {len(pdf.pages)} pages")
            text = ""
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
                else:
                    print(f"Page {i+1} has no text")
            return text
    except ImportError:
        print("pdfplumber not installed")
    except Exception as e:
        print(f"pdfplumber error: {e}")
    return ""

def try_pypdf(pdf_path):
    print("\n--- Trying pypdf ---")
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        print(f"pypdf found {len(reader.pages)} pages")
        text = ""
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
            else:
                print(f"Page {i+1} has no text")
        return text
    except ImportError:
        print("pypdf not installed")
    except Exception as e:
        print(f"pypdf error: {e}")
    return ""

def try_pdfminer(pdf_path):
    print("\n--- Trying pdfminer ---")
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(pdf_path)
        if text:
            print(f"pdfminer extraction successful ({len(text)} chars)")
        else:
            print("pdfminer returned empty text")
        return text
    except ImportError:
        print("pdfminer not installed")
    except Exception as e:
        print(f"pdfminer error: {e}")
    return ""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python read_pdf.py <pdf_path>")
        sys.exit(1)
    
    path = sys.argv[1]
    
    # Try pdfplumber
    text = try_pdfplumber(path)
    if not text:
        text = try_pypdf(path)
    if not text:
        text = try_pdfminer(path)
        
    if text:
        print("\n=== FINAL EXTRACTED TEXT ===")
        print(text)
    else:
        print("\n=== FALIED TO EXTRACT TEXT WITH ALL METHODS ===")
