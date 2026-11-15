"""Pinned, offline Chinese word segmentation. Exact character FTS stays available."""
import logging
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
import jieba

jieba.setLogLevel(logging.ERROR)
_tokenizer = jieba.Tokenizer()
_tokenizer.cache_file = 'shiyue-jieba-0.42.1.cache'
_lock = threading.RLock()
_stopwords = {'的', '了', '和', '与', '是', '在', '如何', '什么', '哪些', '怎么', '请', '一个', '有关', '关于'}


def index_words(text):
    with _lock:
        return ' '.join(word.lower() for word in _tokenizer.cut_for_search(text, HMM=False)
                        if re.search(r'\w', word, re.UNICODE))


def query_words(text):
    with _lock:
        return list(dict.fromkeys(word.lower() for word in _tokenizer.cut(text, HMM=False)
                                 if word not in _stopwords and re.search(r'\w', word, re.UNICODE)))[:40]
