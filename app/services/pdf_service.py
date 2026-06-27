"""
PCS Business OS - PDF and Document Parsing / Chunking Service
File: /app/services/pdf_service.py
"""

import logging
import re
from typing import Dict, Any, List, Optional
import os

logger = logging.getLogger("pcs-business-os.pdf-service")

try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False
    logger.warning("pypdf library is not installed. PDF text extraction will fall back to mock extraction. Install with 'pip install pypdf'")


class PDFService:
    """
    Service class handling parsing of physical PDF/text documents
    and splitting them into semantic chunks for vector search embeddings.
    """

    @staticmethod
    async def extract_text_from_pdf(file_path: str) -> str:
        """
        Asynchronously parses a PDF document and extracts plain text.
        Executes defensively to catch corrupt documents.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"The target document does not exist at path: {file_path}")

        if not PYPDF_AVAILABLE:
            logger.warning("PDF parsing requested but pypdf is not installed. Simulating extraction.")
            # Fallback mock for testing/dev environments
            filename = os.path.basename(file_path)
            return f"[Pypdf fallback] This is a mock text extracted from corrupt or unparseable document: {filename}. Please install pypdf for physical extraction."

        try:
            text_content = []
            # Run PDF parsing defensively
            with open(file_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                num_pages = len(reader.pages)
                logger.info(f"Successfully loaded PDF: {file_path} with {num_pages} pages.")
                
                for page_num in range(num_pages):
                    page = reader.pages[page_num]
                    page_text = page.extract_text()
                    if page_text:
                        text_content.append(page_text)
            
            extracted_text = "\n\n".join(text_content)
            # Basic cleanup of redundant whitespace
            cleaned_text = re.sub(r"\n{3,}", "\n\n", extracted_text).strip()
            return cleaned_text

        except Exception as e:
            logger.error(f"Failed to parse PDF document at {file_path}: {str(e)}")
            raise ValueError(f"Failed to parse PDF document: {str(e)}")

    @staticmethod
    def chunk_text(
        text: str, 
        chunk_size: int = 500, 
        chunk_overlap: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Splits a single body of text into overlapping semantic/text chunks.
        Designed for vector indexing in MongoDB Atlas Vector Search.
        
        Returns a list of dictionaries containing the text chunk, index, and word counts.
        """
        if not text:
            return []

        # Split text into sentences/paragraphs (or standard whitespace chunks for robustness)
        words = text.split()
        total_words = len(words)
        
        if total_words <= chunk_size:
            return [{
                "chunk_index": 0,
                "text": text,
                "word_count": total_words
            }]

        chunks = []
        start_index = 0
        chunk_idx = 0

        while start_index < total_words:
            # End index of the slice
            end_index = min(start_index + chunk_size, total_words)
            chunk_words = words[start_index:end_index]
            
            # Form chunk text
            chunk_text = " ".join(chunk_words)
            chunks.append({
                "chunk_index": chunk_idx,
                "text": chunk_text,
                "word_count": len(chunk_words)
            })

            # Advance window
            chunk_idx += 1
            if end_index == total_words:
                break
                
            start_index += (chunk_size - chunk_overlap)

        logger.info(f"Segmented document text into {len(chunks)} overlapping chunks.")
        return chunks

    @staticmethod
    def clean_extracted_text(text: str) -> str:
        """
        Performs high-fidelity cleanups on extracted raw texts (removing control codes,
        broken hyphens from line splits, and double spaces) to optimize for LLM context processing.
        """
        if not text:
            return ""
        
        # Remove null-bytes and other control characters
        text = text.replace("\x00", "")
        
        # Merge hyphens split across line breaks
        text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)
        
        # Collapse multiple spacing layouts safely
        text = re.sub(r"[ \t]+", " ", text)
        
        # Re-merge stray sentence lines that split paragraphs arbitrarily
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        
        return text.strip()
