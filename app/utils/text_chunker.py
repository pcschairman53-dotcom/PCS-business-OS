"""
PCS Business OS - Intelligent Text Chunking and Segmentation Utilities
File: /app/utils/text_chunker.py
"""

import logging
import re
from typing import Dict, Any, List

logger = logging.getLogger("pcs-business-os.utils.text-chunker")


class RecursiveTextChunker:
    """
    Splits plain text recursively using semantic boundaries (paragraphs, lines, 
    sentences, words) to preserve structural context for LLM grounding.
    """

    @staticmethod
    def split_text(
        text: str,
        chunk_size_words: int = 500,
        chunk_overlap_words: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Recursively partitions raw text into overlapping semantic chunks.
        
        Args:
            text (str): The raw input string.
            chunk_size_words (int): Target maximum number of words per chunk.
            chunk_overlap_words (int): Number of words to overlap between sequential chunks.
            
        Returns:
            List[Dict[str, Any]]: A list of chunk objects containing text content, index, and statistics.
        """
        if not text or not text.strip():
            return []

        # Ensure reasonable overlap limits
        if chunk_overlap_words >= chunk_size_words:
            chunk_overlap_words = chunk_size_words // 5
            logger.warning(
                f"Overlap words configured larger than chunk size. Auto-adjusting overlap to {chunk_overlap_words}"
            )

        # Pre-process text to remove duplicate spacing and null characters
        cleaned_text = text.replace("\x00", "").strip()
        cleaned_text = re.sub(r"[ \t]+", " ", cleaned_text)
        cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)

        # Quick path: if whole document is smaller than target chunk size, return immediately
        words = cleaned_text.split()
        if len(words) <= chunk_size_words:
            return [{
                "chunk_index": 0,
                "text": cleaned_text,
                "word_count": len(words),
                "char_count": len(cleaned_text)
            }]

        # Segment text into logical paragraph components
        paragraphs = cleaned_text.split("\n\n")
        
        chunks: List[Dict[str, Any]] = []
        current_chunk_words: List[str] = []
        current_chunk_idx = 0

        # Sub-split paragraphs that exceed size bounds into sentences
        for paragraph in paragraphs:
            para_words = paragraph.split()
            
            # If paragraph fits comfortably in the remaining space of current chunk, add it
            if len(current_chunk_words) + len(para_words) <= chunk_size_words:
                if current_chunk_words:
                    current_chunk_words.append("\n\n")
                current_chunk_words.extend(para_words)
            else:
                # If paragraph itself is small, close the current chunk and start a new one
                if len(para_words) <= chunk_size_words:
                    if current_chunk_words:
                        chunk_str = " ".join(current_chunk_words).replace(" \n\n ", "\n\n")
                        chunks.append({
                            "chunk_index": current_chunk_idx,
                            "text": chunk_str,
                            "word_count": len(chunk_str.split()),
                            "char_count": len(chunk_str)
                        })
                        current_chunk_idx += 1
                        # Retain overlapping context from the end of the previous chunk
                        current_chunk_words = current_chunk_words[-chunk_overlap_words:] if chunk_overlap_words > 0 else []
                    
                    if current_chunk_words:
                        current_chunk_words.append("\n\n")
                    current_chunk_words.extend(para_words)
                else:
                    # Paragraph is oversized! Sub-split recursively on sentences
                    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
                    for sentence in sentences:
                        sentence_words = sentence.split()
                        
                        if len(current_chunk_words) + len(sentence_words) <= chunk_size_words:
                            current_chunk_words.extend(sentence_words)
                        else:
                            # Close chunk if adding this sentence exceeds size limits
                            if current_chunk_words:
                                chunk_str = " ".join(current_chunk_words).replace(" \n\n ", "\n\n")
                                chunks.append({
                                    "chunk_index": current_chunk_idx,
                                    "text": chunk_str,
                                    "word_count": len(chunk_str.split()),
                                    "char_count": len(chunk_str)
                                })
                                current_chunk_idx += 1
                                current_chunk_words = current_chunk_words[-chunk_overlap_words:] if chunk_overlap_words > 0 else []
                            
                            # If individual sentence is still larger than chunk_size_words, split strictly on space boundaries
                            if len(sentence_words) > chunk_size_words:
                                start_idx = 0
                                while start_idx < len(sentence_words):
                                    end_idx = min(start_idx + chunk_size_words, len(sentence_words))
                                    slice_words = sentence_words[start_idx:end_idx]
                                    slice_str = " ".join(slice_words)
                                    chunks.append({
                                        "chunk_index": current_chunk_idx,
                                        "text": slice_str,
                                        "word_count": len(slice_words),
                                        "char_count": len(slice_str)
                                    })
                                    current_chunk_idx += 1
                                    start_idx += (chunk_size_words - chunk_overlap_words)
                                # Keep remnants of strict splitting
                                current_chunk_words = sentence_words[-chunk_overlap_words:] if chunk_overlap_words > 0 else []
                            else:
                                current_chunk_words.extend(sentence_words)

        # Flush any remaining items in buffer
        if current_chunk_words:
            chunk_str = " ".join(current_chunk_words).replace(" \n\n ", "\n\n")
            # Filter out degenerate chunks containing only spacing or padding remnants
            if len(chunk_str.split()) > (chunk_overlap_words // 2):
                chunks.append({
                    "chunk_index": current_chunk_idx,
                    "text": chunk_str,
                    "word_count": len(chunk_str.split()),
                    "char_count": len(chunk_str)
                })

        logger.info(f"Segmented text content into {len(chunks)} high-fidelity overlapping chunks.")
        return chunks
