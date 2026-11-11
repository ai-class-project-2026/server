"""Trained model folder → model.onnx + tokenizer, for app/moderation/classifier.py.

    !pip install -q "optimum[onnxruntime]"
    !python ml/moderation/export_onnx.py kcelectra-v1 onnx-v1

Then copy onnx-v1/ to the server and set MODERATION_ONNX_PATH=onnx-v1.
"""

import sys

from optimum.onnxruntime import ORTModelForSequenceClassification
from transformers import AutoTokenizer

src, dst = sys.argv[1], sys.argv[2]
ORTModelForSequenceClassification.from_pretrained(src, export=True).save_pretrained(dst)
AutoTokenizer.from_pretrained(src).save_pretrained(dst)
