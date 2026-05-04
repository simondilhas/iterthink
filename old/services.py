import re
import hashlib
import json
from difflib import SequenceMatcher
from typing import List, Tuple, Dict, Optional, Set
from dataclasses import dataclass, asdict
import unicodedata
import time

# Try to import required packages, with fallback
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np
    from rapidfuzz import fuzz
    HAS_TFIDF = True
except ImportError:
    HAS_TFIDF = False
    # Fallback: use Jaccard if packages not available
    print("Warning: scikit-learn, numpy, or rapidfuzz not available. Install with: pip install scikit-learn numpy rapidfuzz")


@dataclass
class Paragraph:
    text: str
    index: int
    tokens: List[str]


@dataclass
class Match:
    old_idx: int
    new_idx: int
    similarity: float
    match_type: str = "similarity"  # "exact_hash", "normalized_hash", "high_similarity", "standard_similarity"
    quality_score: float = 0.0  # Combined quality score for conflict resolution


@dataclass
class DiffParagraph:
    old_text: str
    new_text: str
    status: str  # stable, minor, medium, major, new, deleted
    label: str  # moved, split, merged, added, deleted
    old_index: int
    new_index: int
    sim_score: float = None
    para_id: str = None  # Stable paragraph ID for comment persistence
    old_inline_html: str = None  # Word-level diff HTML for old text
    new_inline_html: str = None  # Word-level diff HTML for new text
    severity: str = None  # Severity badge (low, medium, high)
    carried_from_para_id: str = None  # For comment carry-forward
    # Store raw markdown for editing (before rendering to HTML)
    old_raw_markdown: str = None
    new_raw_markdown: str = None


STOPWORDS = {'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
             'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'that', 'the',
             'to', 'was', 'will', 'with'}


def tokenize(text: str) -> List[str]:
    """Tokenize text, lowercase, remove stopwords."""
    text = text.lower()
    # Remove punctuation and split
    words = re.findall(r'\b\w+\b', text)
    return [w for w in words if w not in STOPWORDS]


def jaccard_similarity(set1: set, set2: set) -> float:
    """Compute Jaccard similarity (fallback when TF-IDF not available)."""
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def compute_tfidf_similarity(old_paras: List[str], new_paras: List[str]) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute TF-IDF cosine similarity matrices.
    
    Returns:
        - word_sim: cosine similarity matrix for word-level TF-IDF (ngram_range=(1,2))
        - char_sim: cosine similarity matrix for char-level TF-IDF (ngram_range=(3,5))
    """
    if not HAS_TFIDF:
        # Fallback: compute simple token-based similarity
        return None, None
    
    if not old_paras or not new_paras:
        return np.zeros((len(old_paras), len(new_paras))), np.zeros((len(old_paras), len(new_paras)))
    
    # Preprocess paragraphs to word and char views
    old_word = [preprocess_text(p)[0] for p in old_paras]
    new_word = [preprocess_text(p)[0] for p in new_paras]
    old_char = [preprocess_text(p)[1] for p in old_paras]
    new_char = [preprocess_text(p)[1] for p in new_paras]
    
    # Word-level TF-IDF: ngram_range=(1,2), analyzer="word"
    word_vectorizer = TfidfVectorizer(
        analyzer='word',
        ngram_range=(1, 2),
        min_df=1,
        lowercase=False  # Already lowercased in preprocess
    )
    
    try:
        word_old_matrix = word_vectorizer.fit_transform(old_word)
        word_new_matrix = word_vectorizer.transform(new_word)
        word_sim = cosine_similarity(word_old_matrix, word_new_matrix)
    except ValueError:
        # Fallback if vectorizer fails (e.g., all empty strings)
        word_sim = np.zeros((len(old_paras), len(new_paras)))
    
    # Char-level TF-IDF: analyzer="char", ngram_range=(3,5)
    char_vectorizer = TfidfVectorizer(
        analyzer='char',
        ngram_range=(3, 5),
        min_df=1,
        lowercase=False  # Already lowercased
    )
    
    try:
        char_old_matrix = char_vectorizer.fit_transform(old_char)
        char_new_matrix = char_vectorizer.transform(new_char)
        char_sim = cosine_similarity(char_old_matrix, char_new_matrix)
    except ValueError:
        # Fallback if vectorizer fails
        char_sim = np.zeros((len(old_paras), len(new_paras)))
    
    return word_sim, char_sim


def compute_token_ratio_matrix(old_paras: List[str], new_paras: List[str], 
                               tfidf_prefilter: Optional[np.ndarray] = None,
                               tfidf_threshold: float = 0.3) -> np.ndarray:
    """
    Compute RapidFuzz token_sort_ratio matrix.
    Returns matrix scaled to 0-1 range.
    
    OPTIMIZATION: If tfidf_prefilter is provided, only compute token ratios for pairs
    where TF-IDF similarity exceeds tfidf_threshold. This dramatically reduces computation
    time from O(n×m) to O(k×m) where k << n.
    
    Args:
        old_paras: List of old paragraph texts
        new_paras: List of new paragraph texts
        tfidf_prefilter: Optional TF-IDF similarity matrix to use as filter
        tfidf_threshold: Minimum TF-IDF score to compute token ratio (default 0.3)
    """
    if not HAS_TFIDF:
        return None
    
    ratios = np.zeros((len(old_paras), len(new_paras)))
    
    # If we have a TF-IDF prefilter, only compute ratios for promising pairs
    if tfidf_prefilter is not None:
        # The prefilter is a combined TF-IDF score (0.6*word + 0.4*char) computed in advance
        # Only compute expensive token ratios for pairs with TF-IDF > threshold
        candidate_count = 0
        total_pairs = len(old_paras) * len(new_paras)
        
        for i, old_para in enumerate(old_paras):
            for j, new_para in enumerate(new_paras):
                # Use TF-IDF as prefilter: only compute token ratio if similarity > threshold
                if i < tfidf_prefilter.shape[0] and j < tfidf_prefilter.shape[1]:
                    tfidf_score = float(tfidf_prefilter[i, j])
                    if tfidf_score > tfidf_threshold:
                        ratio = fuzz.token_sort_ratio(old_para, new_para) / 100.0
                        ratios[i, j] = ratio
                        candidate_count += 1
                    # else: leave as 0.0 (will be filtered out by guardrail in compute_combined_similarity)
        
        # Debug logging (uncomment to see optimization impact)
        # print(f"DEBUG: Computed token ratios for {candidate_count}/{total_pairs} pairs ({100*candidate_count/total_pairs:.1f}%)")
    else:
        # Original behavior: compute all pairs (fallback if no prefilter provided)
        for i, old_para in enumerate(old_paras):
            for j, new_para in enumerate(new_paras):
                ratio = fuzz.token_sort_ratio(old_para, new_para) / 100.0
                ratios[i, j] = ratio
    
    return ratios


def compute_combined_similarity(word_sim: Optional[np.ndarray], char_sim: Optional[np.ndarray], 
                               token_ratio: Optional[np.ndarray], 
                               weight_word: float = 0.6, weight_char: float = 0.4,
                               min_token_ratio: float = 0.50) -> np.ndarray:
    """
    Compute combined similarity score:
    score = 0.6 * cosine_word + 0.4 * cosine_char
    If token_sort_ratio < min_token_ratio, zero out the score (guardrail).
    
    Note: Lowered min_token_ratio from 0.55 to 0.50 to be less aggressive,
    allowing slightly reworded paragraphs to still match.
    """
    if word_sim is None or char_sim is None:
        # Fallback to Jaccard or return zeros
        return np.zeros(word_sim.shape if word_sim is not None else (1, 1))
    
    # Combined score
    combined = weight_word * word_sim + weight_char * char_sim
    
    # Apply guardrail: if token_sort_ratio < min_token_ratio, zero out
    # This prevents false positives for unrelated text
    if token_ratio is not None:
        mask = token_ratio < min_token_ratio
        combined[mask] = 0.0
    
    return combined


def tfidf_similarity_pair(old_text: str, new_text: str) -> float:
    """
    Compute TF-IDF cosine similarity between two text strings.
    Returns combined score (0.6 * word + 0.4 * char) with RapidFuzz guardrail.
    """
    if not HAS_TFIDF:
        # Fallback to simple token-based similarity
        old_tokens = set(tokenize(old_text))
        new_tokens = set(tokenize(new_text))
        return jaccard_similarity(old_tokens, new_tokens)
    
    word_sim, char_sim = compute_tfidf_similarity([old_text], [new_text])
    if word_sim is None or char_sim is None:
        old_tokens = set(tokenize(old_text))
        new_tokens = set(tokenize(new_text))
        return jaccard_similarity(old_tokens, new_tokens)
    
    # Get token ratio
    token_ratio = None
    if HAS_TFIDF:
        ratio = fuzz.token_sort_ratio(old_text, new_text) / 100.0
        token_ratio = np.array([[ratio]])
    
    combined = compute_combined_similarity(word_sim, char_sim, token_ratio)
    return float(combined[0, 0]) if combined.size > 0 else 0.0


def compute_hash(text: str) -> str:
    """Compute SHA256 hash of text."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def normalize_unicode(text: str) -> str:
    """Normalize Unicode to NFKC form."""
    return unicodedata.normalize('NFKC', text)


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace: collapse multiple spaces/tabs/newlines to single space."""
    # Replace all whitespace (spaces, tabs, newlines) with single space
    normalized = re.sub(r'\s+', ' ', text)
    return normalized.strip()


def preprocess_text(text: str) -> Tuple[str, str]:
    """
    Preprocess text into two views:
    - view_word: lowercased, punctuation removed, extra spaces collapsed
    - view_char: lowercased with punctuation kept
    """
    text = normalize_unicode(text)
    # Word view: lowercase, remove punctuation, collapse spaces
    view_word = re.sub(r'[^\w\s]', ' ', text.lower())
    view_word = normalize_whitespace(view_word)
    # Char view: lowercase, keep punctuation
    view_char = text.lower()
    return view_word, view_char


def compute_normalized_hash(text: str) -> str:
    """Compute hash of normalized text (whitespace normalized)."""
    normalized = normalize_whitespace(text)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def compute_quality_score(match: Match, old_idx: int, new_idx: int, old_paras_len: int, new_paras_len: int, 
                          deleted_count: int = 0, added_count: int = 0) -> float:
    """
    Compute quality score for a match, considering:
    - Similarity score (primary)
    - Position proximity (nearby paragraphs more likely to match, but reduced weight when deletions/additions exist)
    - Context similarity (future: could check surrounding paragraphs)
    
    Args:
        match: The match object with similarity score
        old_idx: Old paragraph index
        new_idx: New paragraph index
        old_paras_len: Total old paragraphs
        new_paras_len: Total new paragraphs
        deleted_count: Number of deleted paragraphs (reduces position weight)
        added_count: Number of added paragraphs (reduces position weight)
    """
    similarity = match.similarity
    
    # Position proximity: paragraphs close to each other are more likely matches
    # Normalize by document length
    position_diff = abs(old_idx - new_idx)
    max_diff = max(old_paras_len, new_paras_len)
    position_score = 1.0 - (position_diff / max(max_diff, 1))  # 1.0 if same position, 0.0 if far apart
    
    # Reduce position weight when there are deletions/additions (documents have been restructured)
    # This prevents position from incorrectly penalizing matches when paragraphs shift due to deletions
    total_changes = deleted_count + added_count
    total_paras = max(old_paras_len, new_paras_len)
    change_ratio = total_changes / max(total_paras, 1)
    
    # When change_ratio > 0.05 (5% of paragraphs changed), reduce position weight
    # Similarity should dominate when document structure changes (even minor deletions cause shifts)
    if change_ratio > 0.05:
        # Gradually reduce position weight as change ratio increases
        # At 5% changes: position weight = 15% (slightly reduced)
        # At 10% changes: position weight = 10% 
        # At 20% changes: position weight = 5% (minimal)
        # At 50% changes: position weight = 2% (almost none)
        position_weight = max(0.02, 0.20 * (1.0 - min(change_ratio / 0.3, 0.90)))
        similarity_weight = 1.0 - position_weight
    else:
        # Normal weights when minimal changes
        similarity_weight = 0.85  # Increased similarity weight slightly
        position_weight = 0.15  # Reduced position weight slightly
    
    # Weighted combination: similarity dominant, position as tie-breaker (reduced when deletions exist)
    quality = (similarity * similarity_weight) + (position_score * position_weight)
    
    return quality


def detect_splits_merges(old_paras: List[str], new_paras: List[str], matched_old: Set[int], 
                         matched_new: Set[int], old_tokenized: List[Paragraph], 
                         new_tokenized: List[Paragraph], old_para_ids: Dict[int, int],
                         threshold_split_merge: float = 0.40, threshold_combined: float = 0.60) -> Dict[str, List]:
    """
    Detect split/merge patterns in unmatched paragraphs using TF-IDF similarity.
    
    Returns:
        Dictionary with 'splits' and 'merges' information
    """
    splits = []  # List of (old_idx, [new_idx1, new_idx2, ...])
    merges = []  # List of ([old_idx1, old_idx2, ...], new_idx)
    
    # Check for splits: one old paragraph matches multiple new (unmatched) paragraphs
    unmatched_old = [i for i in range(len(old_paras)) if i not in matched_old and i in old_para_ids]
    unmatched_new = [i for i in range(len(new_paras)) if i not in matched_new]
    
    for old_idx in unmatched_old:
        old_text = old_paras[old_idx]
        matching_new = []
        
        # Check if this old paragraph is similar to multiple new paragraphs
        for new_idx in unmatched_new:
            sim = tfidf_similarity_pair(old_text, new_paras[new_idx])
            if sim >= threshold_split_merge:  # Lower threshold for split detection
                matching_new.append((new_idx, sim))
        
        if len(matching_new) > 1:
            # Sort by similarity
            matching_new.sort(key=lambda x: x[1], reverse=True)
            # If combined similarity is high enough, consider it a split
            if len(matching_new) >= 2:
                total_sim = sum(sim for _, sim in matching_new[:2]) / len(matching_new[:2])
                if total_sim >= threshold_combined:
                    splits.append((old_idx, [new_idx for new_idx, _ in matching_new[:2]]))
    
    # Check for merges: multiple old (unmatched) paragraphs match one new paragraph
    for new_idx in unmatched_new:
        new_text = new_paras[new_idx]
        matching_old = []
        
        for old_idx in unmatched_old:
            if old_idx not in old_para_ids:
                continue
            sim = tfidf_similarity_pair(old_paras[old_idx], new_text)
            if sim >= threshold_split_merge:  # Lower threshold for merge detection
                matching_old.append((old_idx, sim))
        
        if len(matching_old) > 1:
            # Sort by similarity
            matching_old.sort(key=lambda x: x[1], reverse=True)
            # If combined similarity is high enough, consider it a merge
            if len(matching_old) >= 2:
                total_sim = sum(sim for _, sim in matching_old[:2]) / len(matching_old[:2])
                if total_sim >= threshold_combined:
                    merges.append(([old_idx for old_idx, _ in matching_old[:2]], new_idx))
    
    return {"splits": splits, "merges": merges}


def para_id_for(text: str) -> str:
    """
    Generate stable paragraph ID from text content.
    Uses normalized text hash for consistent IDs across versions.
    """
    normalized = normalize_whitespace(text.lower())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:16]


def word_diff_html(old: str, new: str) -> Tuple[str, str]:
    """
    Generate word-level diff HTML for matched paragraphs.
    Returns (old_html, new_html) with <del> and <ins> tags.
    """
    from html import escape
    if not old or not new:
        return old, new
    
    old_words = old.split()
    new_words = new.split()
    sm = SequenceMatcher(a=old_words, b=new_words)
    
    old_html, new_html = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            tok_old = ' '.join(old_words[i1:i2])
            tok_new = ' '.join(new_words[j1:j2])
            old_html.append(escape(tok_old))
            new_html.append(escape(tok_new))
        elif op == 'delete':
            old_html.append(f"<del>{escape(' '.join(old_words[i1:i2]))}</del>")
        elif op == 'insert':
            new_html.append(f"<ins>{escape(' '.join(new_words[j1:j2]))}</ins>")
        elif op == 'replace':
            old_html.append(f"<del>{escape(' '.join(old_words[i1:i2]))}</del>")
            new_html.append(f"<ins>{escape(' '.join(new_words[j1:j2]))}</ins>")
    
    return ' '.join(old_html), ' '.join(new_html)


def split_paragraphs(text: str) -> List[str]:
    """
    Split text into paragraphs matching markdown rendering behavior.
    - Split on double newlines (\\n\\n) - these create separate <p> tags in markdown
    - Single newlines within a paragraph are kept as part of the same paragraph
    - Headers (markdown # headings) are separated as their own paragraphs
    - List items (starting with - or numbered) are separated as their own paragraphs
    - Empty lines are filtered out
    
    This ensures paragraph indices align with HTML <p> tags, fixing comment mapping.
    """
    if not text:
        return []
    
    paragraphs = []
    
    # First split by double newlines (paragraph breaks in markdown)
    # This matches how markdown renders: \\n\\n creates separate <p> tags
    blocks = re.split(r'\n\n+', text)
    
    for block in blocks:
        block = block.strip()
        if not block:
            # Skip empty blocks
            continue
        
        # Split block into lines for analysis
        lines = block.split('\n')
        current_paragraph = []
        
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                # Empty line within block - add as-is (preserves spacing)
                if current_paragraph:
                    current_paragraph.append('')
                continue
            
            # Check if line is a markdown header (starts with #)
            if re.match(r'^#{1,6}\s+', line_stripped):
                # Header - flush current paragraph if any, then add header as separate paragraph
                if current_paragraph:
                    # Join accumulated lines as one paragraph
                    paragraphs.append('\n'.join(current_paragraph))
                    current_paragraph = []
                paragraphs.append(line_stripped)
            
            # Check if line starts with - (markdown bullet list)
            elif re.match(r'^-\s+', line_stripped):
                # List item - flush current paragraph if any, then add list item as separate paragraph
                if current_paragraph:
                    paragraphs.append('\n'.join(current_paragraph))
                    current_paragraph = []
                paragraphs.append(line_stripped)
            
            # Check if line starts with a number followed by . (numbered list)
            elif re.match(r'^\d+\.\s+', line_stripped):
                # List item - flush current paragraph if any, then add list item as separate paragraph
                if current_paragraph:
                    paragraphs.append('\n'.join(current_paragraph))
                    current_paragraph = []
                paragraphs.append(line_stripped)
            
            else:
                # Regular line - accumulate into current paragraph
                # Single newlines within a paragraph are kept (markdown renders them in same <p> tag)
                current_paragraph.append(line_stripped)
        
        # Flush any remaining paragraph content
        if current_paragraph:
            paragraphs.append('\n'.join(current_paragraph))
    
    return paragraphs


def compute_alignment(old_text: str, new_text: str, threshold: float = 0.55) -> List[DiffParagraph]:
    """Compare old and new text at paragraph level using TF-IDF cosine similarity."""
    old_paras = split_paragraphs(old_text)
    new_paras = split_paragraphs(new_text)
    
    if not old_paras or not new_paras:
        return _handle_empty_case(old_paras, new_paras)
    
    # OPTIMIZATION: First match by exact hash (much faster than TF-IDF)
    # This eliminates identical paragraphs before expensive similarity computation
    old_hashes = {i: compute_hash(p) for i, p in enumerate(old_paras)}
    new_hashes = {i: compute_hash(p) for i, p in enumerate(new_paras)}
    
    # Track matched paragraphs
    matched_old: Set[int] = set()
    matched_new: Set[int] = set()
    matches = []
    
    # Build hash lookup tables
    old_hash_to_indices = {}
    for old_idx, hash_val in old_hashes.items():
        if hash_val not in old_hash_to_indices:
            old_hash_to_indices[hash_val] = []
        old_hash_to_indices[hash_val].append(old_idx)
    
    new_hash_to_indices = {}
    for new_idx, hash_val in new_hashes.items():
        if hash_val not in new_hash_to_indices:
            new_hash_to_indices[hash_val] = []
        new_hash_to_indices[hash_val].append(new_idx)
    
    # Match exact hashes first (1:1 priority, position-proximity tiebreaker)
    for hash_val, old_indices in old_hash_to_indices.items():
        if hash_val in new_hash_to_indices:
            new_indices = new_hash_to_indices[hash_val]
            for old_idx in old_indices:
                if old_idx not in matched_old:
                    # Find closest unmatched new paragraph by position
                    best_new_idx = None
                    min_distance = float('inf')
                    for new_idx in new_indices:
                        if new_idx not in matched_new:
                            distance = abs(old_idx - new_idx)
                            if distance < min_distance:
                                min_distance = distance
                                best_new_idx = new_idx
                    
                    if best_new_idx is not None:
                        matches.append(Match(old_idx=old_idx, new_idx=best_new_idx, similarity=1.0))
                        matched_old.add(old_idx)
                        matched_new.add(best_new_idx)
    
    # Only compute TF-IDF for UNMATCHED paragraphs (should be zero for identical versions)
    unmatched_old = [i for i in range(len(old_paras)) if i not in matched_old]
    unmatched_new = [i for i in range(len(new_paras)) if i not in matched_new]
    
    # If all paragraphs matched by hash, skip TF-IDF entirely
    if unmatched_old or unmatched_new:
        unmatched_old_paras = [old_paras[i] for i in unmatched_old]
        unmatched_new_paras = [new_paras[i] for i in unmatched_new]
        
        # Use TF-IDF similarity matrix for batch computation (only for unmatched)
        if HAS_TFIDF and unmatched_old_paras and unmatched_new_paras:
            word_sim_matrix, char_sim_matrix = compute_tfidf_similarity(unmatched_old_paras, unmatched_new_paras)
            
            # OPTIMIZATION: Compute preliminary TF-IDF score (word + char only) to use as filter
            # This allows us to skip expensive token ratio computation for unpromising pairs
            preliminary_tfidf = 0.6 * word_sim_matrix + 0.4 * char_sim_matrix  # Same weights as compute_combined_similarity
            
            # Only compute token ratios for pairs where TF-IDF > threshold (typically 10-20% of pairs)
            token_ratio_matrix = compute_token_ratio_matrix(
                unmatched_old_paras, 
                unmatched_new_paras,
                tfidf_prefilter=preliminary_tfidf,
                tfidf_threshold=0.3
            )
            combined_sim_matrix = compute_combined_similarity(word_sim_matrix, char_sim_matrix, token_ratio_matrix)
        else:
            combined_sim_matrix = None
        
        # Find matches for unmatched paragraphs using TF-IDF
        for i, old_idx in enumerate(unmatched_old):
            for j, new_idx in enumerate(unmatched_new):
                if combined_sim_matrix is not None:
                    sim = float(combined_sim_matrix[i, j])
                else:
                    # Fallback to pairwise TF-IDF or Jaccard
                    sim = tfidf_similarity_pair(old_paras[old_idx], new_paras[new_idx])
                if sim >= threshold:
                    matches.append(Match(old_idx=old_idx, new_idx=new_idx, similarity=sim))
    
    # Greedy select unique matches
    matches.sort(key=lambda m: m.similarity, reverse=True)
    selected_old = set()
    selected_new = set()
    final_matches = []
    
    for match in matches:
        if match.old_idx not in selected_old and match.new_idx not in selected_new:
            selected_old.add(match.old_idx)
            selected_new.add(match.new_idx)
            final_matches.append(match)
    
    # Build result
    result = []
    
    # Handle matched paragraphs
    for match in final_matches:
        old_para = old_paras[match.old_idx]
        new_para = new_paras[match.new_idx]
        
        # Generate stable para_id for matched paragraphs
        # Use old para_id to maintain stability across versions
        para_id = para_id_for(old_para)
        
        # Generate word-level diff HTML for matched rows with changes FIRST
        # This helps us detect if there are actual word-level changes
        old_inline_html = None
        new_inline_html = None
        has_word_changes = False
        
        # Always generate word diff for matched paragraphs to detect changes
        if old_para and new_para:
            old_inline_html, new_inline_html = word_diff_html(old_para, new_para)
            # Check if there are actual changes in the inline diff
            has_word_changes = '<del>' in old_inline_html or '<ins>' in old_inline_html or '<del>' in new_inline_html or '<ins>' in new_inline_html
        
        # Determine status by similarity AND word-level changes
        # Even high similarity (0.95-0.99) with word changes should be "minor", not "stable"
        # Severity badges removed - status badge is sufficient
        if match.similarity >= 0.98 and not has_word_changes:
            # Only mark as stable if similarity is very high AND no word-level changes detected
            status = "stable"
        elif match.similarity >= 0.75 or has_word_changes:
            # Minor change if similarity is decent, OR if there are word-level changes
            status = "minor"
        elif match.similarity >= 0.55:
            status = "medium"
        else:
            status = "major"
        
        severity = None  # No severity badges - status badge is sufficient
        
        # Check for moves (indices differ more than expected)
        label = "unchanged"
        if match.old_idx != match.new_idx:
            label = "moved"
        
        result.append(DiffParagraph(
            old_text=old_para,
            new_text=new_para,
            status=status,
            label=label,
            old_index=match.old_idx,
            new_index=match.new_idx,
            sim_score=match.similarity,
            para_id=para_id,
            old_inline_html=old_inline_html,
            new_inline_html=new_inline_html,
            severity=severity
        ))
    
    # Check for splits/merges
    result = _detect_splits_merges(result, old_paras, new_paras, selected_old, selected_new, final_matches)
    
    # Handle deleted paragraphs
    for old_idx in range(len(old_paras)):
        if old_idx not in selected_old:
            result.append(DiffParagraph(
                old_text=old_paras[old_idx],
                new_text="",
                status="deleted",
                label="deleted",
                old_index=old_idx,
                new_index=-1,
                sim_score=0.0,
                para_id=para_id_for(old_paras[old_idx]),
                severity=None  # No severity badge needed for deleted - status already indicates significance
            ))
    
    # Handle added paragraphs
    for new_idx in range(len(new_paras)):
        if new_idx not in selected_new:
            result.append(DiffParagraph(
                old_text="",
                new_text=new_paras[new_idx],
                status="new",
                label="added",
                old_index=-1,
                new_index=new_idx,
                sim_score=0.0,
                para_id=para_id_for(new_paras[new_idx]),
                severity=None  # No severity badge needed for added - status already indicates significance
            ))
    
    # Sort paragraphs to maintain document flow
    # For deleted paragraphs (new_index=-1), use old_index to preserve their original position
    # For all others, use new_index to show their position in the new document
    # Secondary sort by old_index for stable ordering
    def sort_key(p):
        if p.old_index >= 0 and p.new_index < 0:
            # Deleted: use old position
            return (p.old_index, 0)
        elif p.old_index >= 0:
            # Matched: use new position
            return (p.new_index, p.old_index)
        else:
            # Added: use new position
            return (p.new_index, len(old_paras))
    
    return sorted(result, key=sort_key)


def _handle_empty_case(old_paras: List[str], new_paras: List[str]) -> List[DiffParagraph]:
    """Handle cases where old or new is empty."""
    result = []
    if not old_paras and new_paras:
        for idx, para in enumerate(new_paras):
            result.append(DiffParagraph(
                old_text="",
                new_text=para,
                status="new",
                label="added",
                old_index=-1,
                new_index=idx,
                sim_score=0.0
            ))
    elif old_paras and not new_paras:
        for idx, para in enumerate(old_paras):
            result.append(DiffParagraph(
                old_text=para,
                new_text="",
                status="deleted",
                label="deleted",
                old_index=idx,
                new_index=-1,
                sim_score=0.0
            ))
    return result


def _detect_splits_merges(result: List[DiffParagraph], old_paras: List[str], new_paras: List[str],
                         selected_old: set, selected_new: set, final_matches: List[Match]) -> List[DiffParagraph]:
    """Detect split and merged paragraphs."""
    new_result = []
    
    # Group matches by old index (to detect merges)
    old_to_new = {}
    for match in final_matches:
        if match.old_idx not in old_to_new:
            old_to_new[match.old_idx] = []
        old_to_new[match.old_idx].append(match)
    
    # Group matches by new index (to detect splits)
    new_to_old = {}
    for match in final_matches:
        if match.new_idx not in new_to_old:
            new_to_old[match.new_idx] = []
        new_to_old[match.new_idx].append(match)
    
    # Find merges: multiple old match to one new
    for new_idx, matches in new_to_old.items():
        if len(matches) > 1:
            old_indices = sorted([m.old_idx for m in matches])
            label = f"merged §§{'-'.join(map(str, old_indices))} → §{new_idx}"
            # Keep the best match but change label
            for item in result:
                if item.new_index == new_idx:
                    item.label = "merged"
                    item.old_text = "\n\n".join(old_paras[idx] for idx in old_indices)
                    break
    
    # Find splits: one old matches multiple new (rare, but check adjacent unmatched)
    for old_idx in range(len(old_paras)):
        if old_idx in selected_old:
            matching_new_indices = [m.new_idx for m in final_matches if m.old_idx == old_idx]
            if matching_new_indices:
                # Check if there are unmatched new paragraphs adjacent to matched ones
                new_idx = matching_new_indices[0]
                # This is a heuristic: if indices differ significantly, might be split
                if abs(old_idx - new_idx) > 2:
                    for item in result:
                        if item.old_index == old_idx and item.new_index == new_idx:
                            if item.label == "unchanged":
                                item.label = "split"
                            break
    
    return result


def serialize_diffs(diffs: List[DiffParagraph]) -> str:
    """Serialize list of DiffParagraph objects to JSON string for caching."""
    return json.dumps([asdict(d) for d in diffs], ensure_ascii=False)


def deserialize_diffs(json_str: str) -> List[DiffParagraph]:
    """Deserialize JSON string to list of DiffParagraph objects."""
    data = json.loads(json_str)
    return [DiffParagraph(**item) for item in data]


def inline_diff(old_text: str, new_text: str) -> Tuple[str, str]:
    """Generate inline diff HTML with <ins> and <del> tags."""
    if not old_text:
        return "", new_text
    if not new_text:
        return old_text, ""
    
    try:
        # Tokenize by whitespace
        old_tokens = old_text.split()
        new_tokens = new_text.split()
        
        # Handle empty token lists
        if not old_tokens:
            return old_text, f'<ins>{new_text}</ins>'
        if not new_tokens:
            return f'<del>{old_text}</del>', new_text
        
        matcher = SequenceMatcher(None, old_tokens, new_tokens)
        
        old_html = []
        new_html = []
        
        # Process all opcodes from the matcher
        opcodes = matcher.get_opcodes()
        for tag, i1, i2, j1, j2 in opcodes:
            if tag == 'equal':
                old_html.append(' '.join(old_tokens[i1:i2]))
                new_html.append(' '.join(new_tokens[j1:j2]))
            elif tag == 'replace':
                old_html.append(f'<del>{" ".join(old_tokens[i1:i2])}</del>')
                new_html.append(f'<ins>{" ".join(new_tokens[j1:j2])}</ins>')
            elif tag == 'delete':
                old_html.append(f'<del>{" ".join(old_tokens[i1:i2])}</del>')
            elif tag == 'insert':
                new_html.append(f'<ins>{" ".join(new_tokens[j1:j2])}</ins>')
        
        return ' '.join(old_html), ' '.join(new_html)
    except Exception as e:
        # Fallback: return texts as-is if diff generation fails
        print(f"Error in inline_diff: {e}, old_text length: {len(old_text) if old_text else 0}, new_text length: {len(new_text) if new_text else 0}")
        return old_text, new_text


def ai_generate(base_text: str, prompt: str, system_prompt: str = "You are a helpful writing assistant.", model: str = None, temperature: float = None, db_session=None, project_context: str = None, use_case: str = "general", user_id: int = None) -> tuple[str, dict]:
    """Generate AI content using AI providers (OpenAI, Mistral, Anthropic)."""
    import os
    from app_server.core.encryption_utils import decrypt_value
    from app_server.db.models import AppSettings
    from app_server.services.ai.ai_providers import (
        get_provider_for_model,
        get_model_id,
        get_default_model,
        MODEL_REGISTRY
    )
    
    # Use default model if not specified
    if not model:
        # Check for user's default model preference first
        user_default_model = None
        if db_session and user_id:
            try:
                from app_server.db.models import AppSettings
                default_model_setting = db_session.query(AppSettings).filter(
                    AppSettings.setting_key == "ai_default_model",
                    AppSettings.user_id == user_id
                ).first()
                if default_model_setting and default_model_setting.setting_value:
                    user_default_model = default_model_setting.setting_value
            except Exception as e:
                print(f"Error loading user default model: {e}")
        
        if user_default_model:
            model = user_default_model
        else:
            model = get_default_model(use_case)
    
    # Get model info to determine provider
    provider_name = None
    if model in MODEL_REGISTRY:
        provider_name = MODEL_REGISTRY[model]["provider"]
    else:
        # Try to infer provider from model name
        lower_model = model.lower()
        if model.startswith("gpt-") or model.startswith("o1-"):
            provider_name = "openai"
        elif "infomaniak" in lower_model:
            provider_name = "infomaniak"
        elif "mistral" in lower_model or "pixtral" in lower_model:
            provider_name = "mistral"
        elif "claude" in lower_model:
            provider_name = "anthropic"
        else:
            # Default to OpenAI for backward compatibility
            provider_name = "openai"
    
    # Get API key from database for the specific provider
    api_key = None
    setting_key_map = {
        "openai": "openai_api_key",
        "infomaniak": "infomaniak_api_key",
        "mistral": "mistral_api_key",
        "anthropic": "anthropic_api_key"
    }
    
    setting_key = setting_key_map.get(provider_name, "openai_api_key")
    
    if db_session and user_id:
        try:
            api_key_setting = db_session.query(AppSettings).filter(
                AppSettings.setting_key == setting_key,
                AppSettings.user_id == user_id
            ).first()
            if api_key_setting and api_key_setting.setting_value:
                api_key = decrypt_value(api_key_setting.setting_value)
        except Exception as e:
            print(f"Error loading API key from DB: {e}")
    
    # Fallback to environment variable (for free tier / default API keys)
    # This allows using INFOMANIAK_API_KEY from .env for normal queries, comments, and free tier access
    if not api_key:
        env_key_map = {
            "openai": ["OPENAI_API_KEY"],
            "infomaniak": ["INFOMANIAK_API_KEY"],
            "mistral": ["MISTRAL_API_KEY"],
            "anthropic": ["ANTHROPIC_API_KEY"]
        }
        env_key_candidates = env_key_map.get(provider_name, ["OPENAI_API_KEY"])
        for candidate in env_key_candidates:
            value = os.getenv(candidate)
            if value and value.strip():
                api_key = value
                break
        if api_key and provider_name == "infomaniak":
            print("DEBUG: Using INFOMANIAK_API_KEY from environment for hosted access")
    
    # Check if API key is available
    if not api_key or not api_key.strip():
        provider_display_names = {
            "openai": "OpenAI (ChatGPT)",
            "infomaniak": "Infomaniak (Hosted Mistral)",
            "mistral": "Mistral",
            "anthropic": "Anthropic (Claude)"
        }
        provider_display = provider_display_names.get(provider_name, provider_name.title())
        model_display = MODEL_REGISTRY.get(model, {}).get("display_name", model) if model in MODEL_REGISTRY else model
        
        error_content = f"{base_text}\n\n[AI Generated Response to: {prompt}]\n\n[⚠️ API Key Required: To use {provider_display} ({model_display}), please provide an API key in Assistant Settings ☰ or upgrade your account.]"
        return error_content, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    
    try:
        # Get provider instance
        provider = get_provider_for_model(model, api_key)
        
        # Get actual model ID to send to API
        model_id = get_model_id(model)
        
        # Debug: Log what we're sending to AI
        print(f"DEBUG: Sending to AI - Provider: {provider_name}, Model: {model_id}, Prompt: {prompt[:50]}..., Base text length: {len(base_text)}")
        
        # Get shared instructions if available
        shared_instructions = ""
        try:
            from app_server.core.config_loader import get_shared_instructions
            shared_instructions = get_shared_instructions()
        except Exception:
            pass
        
        # Build the user prompt with context
        # Add shared instructions at the beginning for better visibility
        if shared_instructions:
            user_message = f"{shared_instructions}\n\n"
        else:
            user_message = ""
        
        if not base_text:
            # If no content provided, ask user to provide it
            user_message = user_message + prompt
        else:
            user_message = f"{user_message}{prompt}\n\nContent:\n{base_text}"
        
        # Append project context if provided
        if project_context and project_context.strip():
            user_message = f"{user_message}\n\nProject Context:\n{project_context.strip()}"
        
        # Prepare messages for API
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        # Determine temperature (use provided value, otherwise default to 0.7)
        if temperature is None:
            temperature = 0.7
        
        # Generate response using provider
        response_text = provider.generate(
            messages=messages,
            model=model_id,
            temperature=temperature,
            max_tokens=100000
        )
        
        # Get usage information
        usage = provider.get_last_usage()
        # Add provider name to usage info
        usage["provider"] = provider_name
        
        return response_text.strip(), usage
    except ValueError as e:
        # API key not configured
        provider_display_names = {
            "openai": "OpenAI (ChatGPT)",
            "infomaniak": "Infomaniak (Hosted Mistral)",
            "mistral": "Mistral",
            "anthropic": "Anthropic (Claude)"
        }
        provider_display = provider_display_names.get(provider_name, provider_name.title())
        error_content = f"{base_text}\n\n[⚠️ API Key Required: {str(e)} - Configure it in Assistant Settings ☰]"
        return error_content, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    except Exception as e:
        # Check for rate limit errors specifically
        error_str = str(e)
        error_type_str = str(type(e))
        is_rate_limit = False
        
        # Check for various rate limit indicators (429, rate_limit_exceeded, RateLimitError, etc.)
        if (
            "429" in error_str or 
            "rate_limit" in error_str.lower() or 
            "RateLimitError" in error_type_str or
            "rate_limit_exceeded" in error_str.lower() or
            (hasattr(e, 'status_code') and e.status_code == 429)
        ):
            is_rate_limit = True
        
        if is_rate_limit:
            # Extract retry information if available
            retry_info = ""
            if hasattr(e, 'response') and hasattr(e.response, 'headers'):
                retry_after = e.response.headers.get('Retry-After')
                if retry_after:
                    retry_info = f" Please retry after {retry_after} seconds."
            
            error_content = (
                f"{base_text}\n\n"
                f"[⚠️ Rate Limit Exceeded: The AI service (Infomaniak) has rate-limited requests.{retry_info} "
                f"Please wait a few moments before trying again. "
                f"See: https://developer.infomaniak.com/docs/api/post/1/llm/{{product_id}}]"
            )
            print(f"AI Rate Limit Error (429): {error_str}")
        else:
            error_content = f"{base_text}\n\n[AI Error: {error_str}]"
            print(f"AI Error: {error_str}")
        
        return error_content, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def generate_commit_message(old_text: str, new_text: str) -> str:
    """Auto-generate a commit message based on diff analysis."""
    if not old_text:
        return "first version"
    
    if not new_text:
        return "Deleted all content"
    
    # Analyze the diff
    diffs = compute_alignment(old_text, new_text)
    
    # Count changes
    added_count = sum(1 for d in diffs if d.status == "new")
    deleted_count = sum(1 for d in diffs if d.status == "deleted")
    modified_count = sum(1 for d in diffs if d.status not in ["stable", "new", "deleted"])
    
    # Generate message based on changes
    messages = []
    
    if added_count > 0:
        messages.append(f"Added {added_count} paragraph{'s' if added_count > 1 else ''}")
    
    if deleted_count > 0:
        messages.append(f"Removed {deleted_count} paragraph{'s' if deleted_count > 1 else ''}")
    
    if modified_count > 0:
        if modified_count == 1:
            # Check if it's minor or major change
            change = next((d for d in diffs if d.status not in ["stable", "new", "deleted"]), None)
            if change:
                if change.status in ["minor", "medium"]:
                    messages.append("Updated 1 section")
                else:
                    messages.append("Significantly revised 1 section")
        else:
            messages.append(f"Updated {modified_count} sections")
    
    if modified_count > 0 and (added_count > 0 or deleted_count > 0):
        # Get a sample of what changed
        changes = [d for d in diffs if d.status not in ["stable", "new", "deleted"]]
        if changes:
            change = changes[0]
            # Get first sentence of changed text
            first_sentence = change.new_text.split('.')[0] if change.new_text else ""
            if len(first_sentence) < 60:
                messages.append(f"Modified: {first_sentence}")
    
    if not messages:
        return "Minor edits"
    
    return ". ".join(messages)


def generate_ai_commit_message(old_text: str, new_text: str, db_session=None, project_context: str = None, user_id: int = None) -> str:
    """Generate an AI-powered commit message based on diff analysis. Returns a short, high-level description."""
    if not old_text:
        return "first version"
    
    if not new_text:
        return "Deleted all content"
    
    # Analyze the diff (compute_alignment is already imported at module level)
    diffs = compute_alignment(old_text, new_text)
    
    # Count high-level change statistics
    added_count = sum(1 for d in diffs if d.status == "new")
    deleted_count = sum(1 for d in diffs if d.status == "deleted")
    modified_count = sum(1 for d in diffs if d.status not in ["stable", "new", "deleted"])
    
    # Build a high-level summary of changes (focus on meaning, not details)
    changes_summary = []
    
    # Group changes by type and extract key themes
    if added_count > 0:
        # Get a sample of added content to understand theme
        added_samples = [d.new_text[:150] for d in diffs if d.status == "new"][:3]
        changes_summary.append(f"Added {added_count} new section(s): {', '.join([s.split('.')[0][:60] for s in added_samples])}")
    
    if deleted_count > 0:
        changes_summary.append(f"Removed {deleted_count} section(s)")
    
    if modified_count > 0:
        # Get sample of modified content to understand what changed
        modified_samples = []
        for diff in diffs:
            if diff.status not in ["stable", "new", "deleted"] and len(modified_samples) < 3:
                # Extract key words/phrases that changed
                old_words = set(diff.old_text.lower().split()[:20])
                new_words = set(diff.new_text.lower().split()[:20])
                added_words = new_words - old_words
                if added_words:
                    modified_samples.append(f"updated section with focus on: {', '.join(list(added_words)[:3])}")
        
        if modified_samples:
            changes_summary.append(f"Modified {modified_count} section(s): {'; '.join(modified_samples[:2])}")
        else:
            changes_summary.append(f"Modified {modified_count} section(s)")
    
    changes_text = "\n".join(changes_summary) if changes_summary else "Minor edits or formatting changes"
    
    # Create prompt for AI - emphasize short, high-level, single sentence
    system_prompt = "You are a helpful assistant that generates very concise, high-level commit messages for document versions. Your messages must be exactly one sentence, focusing on the overall change rather than details."
    
    user_prompt = f"""Generate a single, high-level sentence (maximum 80 characters) that describes what changed in this document version. Focus on the overall change, not specific details.

Change summary:
{changes_text}

Examples of good commit messages:
- "Expanded introduction with new research findings"
- "Restructured conclusion and added recommendations"
- "Added authentication section and updated user flow"
- "Revised methodology with clearer explanations"

Generate only the commit message, nothing else:"""

    # Generate using AI (use default model, no specific use_case for commit messages)
    ai_message, _ = ai_generate("", user_prompt, system_prompt, model=None, db_session=db_session, project_context=project_context, use_case="general", user_id=user_id)
    
    # Fallback to rule-based if AI fails
    if not ai_message or ai_message.startswith("[") or "Error" in ai_message:
        return generate_commit_message(old_text, new_text)
    
    # Clean up the AI response (remove quotes if wrapped, extract first sentence)
    ai_message = ai_message.strip().strip('"').strip("'")
    
    # Extract first sentence if multiple sentences
    first_period = ai_message.find('.')
    if first_period > 0 and first_period < len(ai_message) - 1:
        ai_message = ai_message[:first_period].strip()
    
    # Limit length to ensure it's short
    if len(ai_message) > 100:
        ai_message = ai_message[:97] + "..."
    
    return ai_message


def extract_headings(markdown_content: str) -> List[Tuple[int, str]]:
    """
    Extract headings from markdown content.
    Returns list of tuples (level, text) where level is the heading level (1-6)
    and text is the heading text.
    """
    headings = []
    if not markdown_content:
        return headings
    
    lines = markdown_content.split('\n')
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        
        # Check for ATX-style headings (# ## ### etc)
        # Pattern: 1-6 # symbols, followed by optional whitespace, then heading text
        # This handles: "# Heading", "## Heading", "###Heading", etc.
        match = re.match(r'^(#{1,6})\s*(.+)$', stripped)
        if match:
            level = len(match.group(1))
            text = match.group(2).strip()
            # Remove any trailing # symbols that might be part of the heading style
            text = text.rstrip('#').strip()
            # Only add if there's actual text content
            if text:
                headings.append((level, text))
    
    return headings


def assign_paragraph_ids(document_id: int, parent_version_id: Optional[int], new_content: str, db_session, threshold: float = 0.50) -> Dict[int, int]:
    """
    Assign stable paragraph IDs to paragraphs in new content using multi-stage matching.
    
    Stages:
    1. Pre-process: Normalize whitespace, compute hashes
    2. Exact hash match (position-independent): Match all exact hashes first
    3. Near-exact match (normalized hash): Match with whitespace normalized
    4. High-confidence similarity (0.90+): Match highly similar paragraphs
    5. Standard similarity (0.55+): Match with greedy selection + quality scoring
    6. Split/merge detection: Analyze unmatched paragraphs for split/merge patterns
    7. Mark deletions: Old paragraphs not matched
    8. Mark additions: New paragraphs not matched
    
    Args:
        document_id: ID of the document
        parent_version_id: ID of parent version (None for first version)
        new_content: New content text
        db_session: Database session
        threshold: Similarity threshold for matching (default 0.50, lowered to improve matching of slightly edited paragraphs)
    
    Returns:
        Dictionary mapping paragraph_index -> paragraph_id
    """
    from app_server.db.models import Paragraph as ParagraphModel, VersionParagraph, Version
    from datetime import datetime, UTC
    
    # TIMER START - Initialize all timer variables
    start_time = time.time()
    hash_time = tokenize_time = hash_match_time = norm_hash_time = 0
    high_sim_time = standard_sim_time = split_merge_time = deletion_time = addition_time = 0
    
    # Split new content into paragraphs
    new_paras = split_paragraphs(new_content)
    
    # If no parent version, all paragraphs are new
    if not parent_version_id:
        result = {}
        for idx, para_text in enumerate(new_paras):
            new_para = ParagraphModel(
                document_id=document_id,
                created_at=datetime.now(UTC)
            )
            db_session.add(new_para)
            db_session.flush()
            result[idx] = new_para.id
        total_time = time.time() - start_time
        if total_time > 0.001:  # Only print if took >1ms
            print(f"⏱️  assign_paragraph_ids: no parent, created {len(result)} new paras, {total_time*1000:.2f}ms")
        return result
    
    # Get parent version and its paragraph mappings
    parent_version = db_session.query(Version).filter(Version.id == parent_version_id).first()
    if not parent_version:
        # Parent not found, treat as new document
        result = {}
        for idx, para_text in enumerate(new_paras):
            new_para = ParagraphModel(
                document_id=document_id,
                created_at=datetime.now(UTC)
            )
            db_session.add(new_para)
            db_session.flush()
            result[idx] = new_para.id
        total_time = time.time() - start_time
        if total_time > 0.001:
            print(f"⏱️  assign_paragraph_ids: parent not found, created {len(result)} new paras, {total_time*1000:.2f}ms")
        return result
    
    # Load parent version content from database
    old_content = parent_version.content or ""
    old_paras = split_paragraphs(old_content) if old_content else []
    
    # Get existing paragraph IDs from parent version
    parent_vps = db_session.query(VersionParagraph).filter(
        VersionParagraph.version_id == parent_version_id
    ).order_by(VersionParagraph.paragraph_index).all()
    
    # If parent version has no paragraph mappings, treat all as new paragraphs
    if not parent_vps:
        result = {}
        for idx, para_text in enumerate(new_paras):
            new_para = ParagraphModel(
                document_id=document_id,
                created_at=datetime.now(UTC)
            )
            db_session.add(new_para)
            db_session.flush()
            result[idx] = new_para.id
        total_time = time.time() - start_time
        if total_time > 0.001:
            print(f"⏱️  assign_paragraph_ids: no mappings, created {len(result)} new paras, {total_time*1000:.2f}ms")
        return result
    
    # Create mapping: old_index -> paragraph_id
    old_para_ids = {}
    for vp in parent_vps:
        if vp.paragraph_index < len(old_paras):
            old_para_ids[vp.paragraph_index] = vp.paragraph_id
    
    # ============================================
    # STAGE 1: PRE-PROCESS - Compute hashes
    # ============================================
    # Handle empty paragraph lists
    if not old_paras or not new_paras:
        result = {}
        if not new_paras:
            # All old paragraphs are deleted
            for old_idx in old_para_ids.keys():
                para_id = old_para_ids[old_idx]
                para = db_session.query(ParagraphModel).filter(ParagraphModel.id == para_id).first()
                if para and not para.deleted_at:
                    para.deleted_at = datetime.now(UTC)
            total_time = time.time() - start_time
            if total_time > 0.001:
                print(f"⏱️  assign_paragraph_ids: all deleted, {total_time*1000:.2f}ms")
            return result
        
        # Only new paragraphs (no old to match)
        for idx in range(len(new_paras)):
            new_para = ParagraphModel(
                document_id=document_id,
                created_at=datetime.now(UTC)
            )
            db_session.add(new_para)
            db_session.flush()
            result[idx] = new_para.id
        total_time = time.time() - start_time
        if total_time > 0.001:
            print(f"⏱️  assign_paragraph_ids: only new paras, {total_time*1000:.2f}ms")
        return result
    
    hash_time = time.time()
    old_hashes = {i: compute_hash(p) for i, p in enumerate(old_paras)}
    new_hashes = {i: compute_hash(p) for i, p in enumerate(new_paras)}
    old_normalized_hashes = {i: compute_normalized_hash(p) for i, p in enumerate(old_paras)}
    new_normalized_hashes = {i: compute_normalized_hash(p) for i, p in enumerate(new_paras)}
    hash_time = time.time() - hash_time
    
    # Tokenize paragraphs for fallback matching (if TF-IDF not available)
    # Note: TF-IDF uses raw paragraph text, tokenization only for Jaccard fallback
    tokenize_time = time.time()
    old_tokenized = [Paragraph(p, i, set(tokenize(p))) for i, p in enumerate(old_paras)]
    new_tokenized = [Paragraph(p, i, set(tokenize(p))) for i, p in enumerate(new_paras)]
    tokenize_time = time.time() - tokenize_time
    
    # Track matched paragraphs
    matched_old: Set[int] = set()
    matched_new: Set[int] = set()
    all_matches: List[Match] = []
    result = {}
    
    # ============================================
    # STAGE 2: EXACT HASH MATCH (position-independent)
    # ============================================
    hash_match_time = time.time()
    # Build hash lookup tables (position-independent matching)
    old_hash_to_indices = {}
    for old_idx, hash_val in old_hashes.items():
        if old_idx in old_para_ids:
            if hash_val not in old_hash_to_indices:
                old_hash_to_indices[hash_val] = []
            old_hash_to_indices[hash_val].append(old_idx)
    
    new_hash_to_indices = {}
    for new_idx, hash_val in new_hashes.items():
        if hash_val not in new_hash_to_indices:
            new_hash_to_indices[hash_val] = []
        new_hash_to_indices[hash_val].append(new_idx)
    
    # Match exact hashes (1:1 priority, but allow 1:many if needed)
    for hash_val, old_indices in old_hash_to_indices.items():
        if hash_val in new_hash_to_indices:
            new_indices = new_hash_to_indices[hash_val]
            # Simple 1:1 matching for exact hashes (prioritize position proximity)
            for old_idx in old_indices:
                if old_idx not in matched_old and old_idx in old_para_ids:
                    # Find closest unmatched new paragraph
                    best_new_idx = None
                    min_distance = float('inf')
                    for new_idx in new_indices:
                        if new_idx not in matched_new:
                            distance = abs(old_idx - new_idx)
                            if distance < min_distance:
                                min_distance = distance
                                best_new_idx = new_idx
                    
                    if best_new_idx is not None:
                        match = Match(
                            old_idx=old_idx,
                            new_idx=best_new_idx,
                            similarity=1.0,
                            match_type="exact_hash"
                        )
                        match.quality_score = 1.0
                        all_matches.append(match)
                        matched_old.add(old_idx)
                        matched_new.add(best_new_idx)
                        result[best_new_idx] = old_para_ids[old_idx]
    hash_match_time = time.time() - hash_match_time
    
    # ============================================
    # STAGE 3: NEAR-EXACT MATCH (normalized hash)
    # ============================================
    norm_hash_time = time.time()
    old_norm_hash_to_indices = {}
    for old_idx, hash_val in old_normalized_hashes.items():
        if old_idx not in matched_old and old_idx in old_para_ids:
            if hash_val not in old_norm_hash_to_indices:
                old_norm_hash_to_indices[hash_val] = []
            old_norm_hash_to_indices[hash_val].append(old_idx)
    
    new_norm_hash_to_indices = {}
    for new_idx, hash_val in new_normalized_hashes.items():
        if new_idx not in matched_new:
            if hash_val not in new_norm_hash_to_indices:
                new_norm_hash_to_indices[hash_val] = []
            new_norm_hash_to_indices[hash_val].append(new_idx)
    
    # Match normalized hashes (whitespace-only differences)
    for hash_val, old_indices in old_norm_hash_to_indices.items():
        if hash_val in new_norm_hash_to_indices:
            new_indices = new_norm_hash_to_indices[hash_val]
            for old_idx in old_indices:
                if old_idx not in matched_old:
                    best_new_idx = None
                    min_distance = float('inf')
                    for new_idx in new_indices:
                        if new_idx not in matched_new:
                            distance = abs(old_idx - new_idx)
                            if distance < min_distance:
                                min_distance = distance
                                best_new_idx = new_idx
                    
                    if best_new_idx is not None:
                        match = Match(
                            old_idx=old_idx,
                            new_idx=best_new_idx,
                            similarity=0.95,  # Very high similarity for normalized match
                            match_type="normalized_hash"
                        )
                        match.quality_score = compute_quality_score(match, old_idx, best_new_idx, len(old_paras), len(new_paras), 0, 0)
                        all_matches.append(match)
                        matched_old.add(old_idx)
                        matched_new.add(best_new_idx)
                        result[best_new_idx] = old_para_ids[old_idx]
    norm_hash_time = time.time() - norm_hash_time
    
    # ============================================
    # STAGE 4: HIGH-CONFIDENCE SIMILARITY (0.90+) - TF-IDF
    # ============================================
    high_sim_time = time.time()
    unmatched_old = [i for i in range(len(old_paras)) if i not in matched_old and i in old_para_ids]
    unmatched_new = [i for i in range(len(new_paras)) if i not in matched_new]
    
    # Estimate deletion/additions for quality score adjustment
    # Count unmatched paragraphs that will likely be deleted/added
    estimated_deletions = len(unmatched_old)
    estimated_additions = len(unmatched_new)
    
    # Compute TF-IDF similarity matrix for unmatched paragraphs only (efficient)
    unmatched_old_paras = [old_paras[i] for i in unmatched_old]
    unmatched_new_paras = [new_paras[i] for i in unmatched_new]
    
    # Use batch TF-IDF for efficiency
    if HAS_TFIDF and unmatched_old_paras and unmatched_new_paras:
        word_sim_matrix, char_sim_matrix = compute_tfidf_similarity(unmatched_old_paras, unmatched_new_paras)
        
        # OPTIMIZATION: Use TF-IDF as prefilter to reduce token ratio computation
        preliminary_tfidf = 0.6 * word_sim_matrix + 0.4 * char_sim_matrix
        token_ratio_matrix = compute_token_ratio_matrix(
            unmatched_old_paras, 
            unmatched_new_paras,
            tfidf_prefilter=preliminary_tfidf,
            tfidf_threshold=0.3
        )
        combined_sim_matrix = compute_combined_similarity(word_sim_matrix, char_sim_matrix, token_ratio_matrix)
    else:
        # Fallback: compute pairwise
        combined_sim_matrix = None
    
    high_sim_matches = []
    for i, old_idx in enumerate(unmatched_old):
        for j, new_idx in enumerate(unmatched_new):
            if combined_sim_matrix is not None:
                sim = float(combined_sim_matrix[i, j])
            else:
                # Fallback to pairwise TF-IDF or Jaccard
                sim = tfidf_similarity_pair(old_paras[old_idx], new_paras[new_idx])
            
            if sim >= 0.90:
                match = Match(
                    old_idx=old_idx,
                    new_idx=new_idx,
                    similarity=sim,
                    match_type="high_similarity"
                )
                match.quality_score = compute_quality_score(match, old_idx, new_idx, len(old_paras), len(new_paras), 
                                                           estimated_deletions, estimated_additions)
                high_sim_matches.append(match)
    
    # Greedy selection for high-similarity matches
    high_sim_matches.sort(key=lambda m: m.quality_score, reverse=True)
    for match in high_sim_matches:
        if match.old_idx not in matched_old and match.new_idx not in matched_new:
            matched_old.add(match.old_idx)
            matched_new.add(match.new_idx)
            all_matches.append(match)
            result[match.new_idx] = old_para_ids[match.old_idx]
    high_sim_time = time.time() - high_sim_time
    
    # ============================================
    # STAGE 5: STANDARD SIMILARITY (0.55+) with quality scoring - TF-IDF
    # ============================================
    standard_sim_time = time.time()
    unmatched_old = [i for i in range(len(old_paras)) if i not in matched_old and i in old_para_ids]
    unmatched_new = [i for i in range(len(new_paras)) if i not in matched_new]
    
    # Update estimates after stage 4 matches
    estimated_deletions = len(unmatched_old)
    estimated_additions = len(unmatched_new)
    
    # Update unmatched lists
    unmatched_old_paras = [old_paras[i] for i in unmatched_old]
    unmatched_new_paras = [new_paras[i] for i in unmatched_new]
    
    # Recompute similarity matrix for remaining unmatched
    if HAS_TFIDF and unmatched_old_paras and unmatched_new_paras:
        word_sim_matrix, char_sim_matrix = compute_tfidf_similarity(unmatched_old_paras, unmatched_new_paras)
        
        # OPTIMIZATION: Use TF-IDF as prefilter to reduce token ratio computation
        preliminary_tfidf = 0.6 * word_sim_matrix + 0.4 * char_sim_matrix
        token_ratio_matrix = compute_token_ratio_matrix(
            unmatched_old_paras, 
            unmatched_new_paras,
            tfidf_prefilter=preliminary_tfidf,
            tfidf_threshold=0.3
        )
        combined_sim_matrix = compute_combined_similarity(word_sim_matrix, char_sim_matrix, token_ratio_matrix)
    else:
        combined_sim_matrix = None
    
    standard_matches = []
    for i, old_idx in enumerate(unmatched_old):
        for j, new_idx in enumerate(unmatched_new):
            if combined_sim_matrix is not None:
                sim = float(combined_sim_matrix[i, j])
            else:
                # Fallback to pairwise TF-IDF or Jaccard
                sim = tfidf_similarity_pair(old_paras[old_idx], new_paras[new_idx])
            
            if sim >= threshold:
                match = Match(
                    old_idx=old_idx,
                    new_idx=new_idx,
                    similarity=sim,
                    match_type="standard_similarity"
                )
                match.quality_score = compute_quality_score(match, old_idx, new_idx, len(old_paras), len(new_paras),
                                                           estimated_deletions, estimated_additions)
                standard_matches.append(match)
    
    # Greedy selection by quality score (not just similarity)
    standard_matches.sort(key=lambda m: m.quality_score, reverse=True)
    for match in standard_matches:
        if match.old_idx not in matched_old and match.new_idx not in matched_new:
            matched_old.add(match.old_idx)
            matched_new.add(match.new_idx)
            all_matches.append(match)
            result[match.new_idx] = old_para_ids[match.old_idx]
    standard_sim_time = time.time() - standard_sim_time
    
    # ============================================
    # STAGE 6: SPLIT/MERGE DETECTION
    # ============================================
    split_merge_time = time.time()
    split_merge_info = detect_splits_merges(
        old_paras, new_paras, matched_old, matched_new,
        old_tokenized, new_tokenized, old_para_ids,
        threshold_split_merge=0.40, threshold_combined=0.60
    )
    
    # Handle splits: assign same paragraph_id to first new paragraph, create new IDs for others
    for old_idx, new_indices in split_merge_info["splits"]:
        if old_idx not in matched_old and old_idx in old_para_ids:
            if new_indices:
                # First new paragraph gets the old paragraph_id
                first_new_idx = new_indices[0]
                if first_new_idx not in matched_new:
                    matched_old.add(old_idx)
                    matched_new.add(first_new_idx)
                    result[first_new_idx] = old_para_ids[old_idx]
                    # Remaining splits get new paragraph IDs
                    for other_new_idx in new_indices[1:]:
                        if other_new_idx not in matched_new:
                            new_para = ParagraphModel(
                                document_id=document_id,
                                created_at=datetime.now(UTC)
                            )
                            db_session.add(new_para)
                            db_session.flush()
                            matched_new.add(other_new_idx)
                            result[other_new_idx] = new_para.id
    
    # Handle merges: assign paragraph_id of first old paragraph, mark others as contributors
    for old_indices, new_idx in split_merge_info["merges"]:
        if new_idx not in matched_new and old_indices:
            first_old_idx = old_indices[0]
            if first_old_idx in old_para_ids:
                matched_new.add(new_idx)
                if first_old_idx not in matched_old:
                    matched_old.add(first_old_idx)
                result[new_idx] = old_para_ids[first_old_idx]
                # Mark other old paragraphs as matched (they merged into this one)
                for other_old_idx in old_indices[1:]:
                    if other_old_idx not in matched_old and other_old_idx in old_para_ids:
                        matched_old.add(other_old_idx)
    split_merge_time = time.time() - split_merge_time
    
    # ============================================
    # STAGE 7: MARK DELETIONS
    # ============================================
    deletion_time = time.time()
    for old_idx in range(len(old_paras)):
        if old_idx in old_para_ids and old_idx not in matched_old:
            para_id = old_para_ids[old_idx]
            para = db_session.query(ParagraphModel).filter(ParagraphModel.id == para_id).first()
            if para and not para.deleted_at:
                para.deleted_at = datetime.now(UTC)
    deletion_time = time.time() - deletion_time
    
    # ============================================
    # STAGE 8: MARK ADDITIONS (create new paragraph IDs)
    # ============================================
    addition_time = time.time()
    for new_idx in range(len(new_paras)):
        if new_idx not in matched_new:
            new_para = ParagraphModel(
                document_id=document_id,
                created_at=datetime.now(UTC)
            )
            db_session.add(new_para)
            db_session.flush()
            result[new_idx] = new_para.id
    addition_time = time.time() - addition_time
    
    # TIMER END - Print results
    total_time = time.time() - start_time
    print(f"⏱️  assign_paragraph_ids timing:"
          f" hash={hash_time*1000:.2f}ms,"
          f" tokenize={tokenize_time*1000:.2f}ms,"
          f" hash_match={hash_match_time*1000:.2f}ms,"
          f" norm_hash={norm_hash_time*1000:.2f}ms,"
          f" high_sim={high_sim_time*1000:.2f}ms,"
          f" standard_sim={standard_sim_time*1000:.2f}ms,"
          f" split_merge={split_merge_time*1000:.2f}ms,"
          f" deletion={deletion_time*1000:.2f}ms,"
          f" addition={addition_time*1000:.2f}ms,"
          f" total={total_time*1000:.2f}ms")
    
    return result



