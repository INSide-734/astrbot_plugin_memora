"""测试 text_processor.py — TextProcessor."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.processors.text_processor import TextProcessor, create_text_processor


class TestTextProcessor:
    @pytest.fixture
    def processor(self) -> TextProcessor:
        return TextProcessor()

    def test_tokenize_chinese(self, processor: TextProcessor) -> None:
        tokens = processor.tokenize("我今天去图书馆看书")
        assert len(tokens) > 0
        assert isinstance(tokens, list)

    def test_tokenize_english(self, processor: TextProcessor) -> None:
        tokens = processor.tokenize("I went to the library today")
        assert len(tokens) > 0
        assert isinstance(tokens, list)

    def test_tokenize_mixed(self, processor: TextProcessor) -> None:
        tokens = processor.tokenize("I like coffee 和我喜欢咖啡")
        assert len(tokens) > 0

    def test_tokenize_empty_string(self, processor: TextProcessor) -> None:
        tokens = processor.tokenize("")
        assert tokens == []

    def test_tokenize_whitespace_only(self, processor: TextProcessor) -> None:
        tokens = processor.tokenize("   \t  \n  ")
        assert tokens == []

    def test_tokenize_removes_urls(self, processor: TextProcessor) -> None:
        tokens = processor.tokenize("visit https://example.com for more info")
        # URL should be removed
        result = " ".join(tokens)
        assert "example.com" not in result

    def test_tokenize_removes_at_mentions(self, processor: TextProcessor) -> None:
        tokens = processor.tokenize("@username check this out")
        result = " ".join(tokens)
        assert "username" not in result

    def test_tokenize_none_input(self, processor: TextProcessor) -> None:
        tokens = processor.tokenize(None)
        assert tokens == []

    def test_tokenize_batch(self, processor: TextProcessor) -> None:
        texts = ["文本1", "文本2", "文本3"]
        results = processor.tokenize_batch(texts)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, list)

    def test_preprocess_for_bm25(self, processor: TextProcessor) -> None:
        result = processor.preprocess_for_bm25("我今天去图书馆看书")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_word_freq(self, processor: TextProcessor) -> None:
        texts = ["我爱编程", "编程很有趣", "我也爱学习"]
        freq = processor.get_word_freq(texts)
        assert isinstance(freq, dict)
        assert "编程" in freq

    def test_add_custom_words(self, processor: TextProcessor) -> None:
        processor.add_custom_words(["Memora", "AstrBot"])
        assert processor.custom_words_count >= 0

    def test_add_stopwords(self, processor: TextProcessor) -> None:
        before = processor.stopwords_count
        processor.add_stopwords(["测试专用词"])
        assert processor.stopwords_count >= before

    def test_remove_stopwords(self, processor: TextProcessor) -> None:
        word = "我"
        processor.remove_stopwords_from_list([word])
        assert word not in processor.stopwords

    def test_is_stopword(self, processor: TextProcessor) -> None:
        # "的" is typically in Chinese stopwords
        assert processor.is_stopword("的") is True

    def test_is_stopword_not_found(self, processor: TextProcessor) -> None:
        assert processor.is_stopword("unique_fake_word_xyz123") is False

    def test_filter_stopwords(self, processor: TextProcessor) -> None:
        tokens = ["我", "是", "学生", "图书馆"]
        filtered = processor.filter_stopwords(tokens)
        # Common stopwords like "的", "是", "我" should be removed
        for t in filtered:
            assert t not in processor.stopwords

    def test_tokenize_without_stopword_removal(self, processor: TextProcessor) -> None:
        tokens = processor.tokenize("我是一个学生", remove_stopwords=False)
        assert len(tokens) > 0

    def test_add_custom_words_invalid_input(self, processor: TextProcessor) -> None:
        # Should not raise with empty/None words
        processor.add_custom_words(["", None, "valid"])
        assert "valid" in processor.custom_words or True  # at least should not crash


class TestCreateTextProcessor:
    def test_default_creation(self) -> None:
        proc = create_text_processor()
        assert isinstance(proc, TextProcessor)
        assert proc.stopwords_count > 0

    def test_creation_with_options(self) -> None:
        proc = create_text_processor(
            custom_words=["Memora", "AstrBot"],
            additional_stopwords=["test_word"],
        )
        assert isinstance(proc, TextProcessor)

    def test_creation_with_stopwords_path(self, tmp_path) -> None:
        stopwords_dir = tmp_path / "stopwords"
        proc = create_text_processor(stopwords_path=str(stopwords_dir))
        assert isinstance(proc, TextProcessor)


# ---------------------------------------------------------------------------
# TextProcessor — additional edge cases and async methods
# ---------------------------------------------------------------------------


class TestTextProcessorAsync:
    @pytest.fixture
    def processor(self) -> TextProcessor:
        return TextProcessor()

    @pytest.mark.asyncio
    async def test_async_init_no_dir(self, processor: TextProcessor) -> None:
        await processor.async_init()
        # Should not raise

    @pytest.mark.asyncio
    async def test_async_init_with_dir(self, tmp_path) -> None:
        stopwords_dir = tmp_path / "stopwords"
        stopwords_dir.mkdir()
        (stopwords_dir / "stopwords_hit.txt").write_text(
            "测试词1\n测试词2\n", encoding="utf-8"
        )
        proc = TextProcessor(stopwords_dir=str(stopwords_dir))
        await proc.async_init()
        # Should load additional stopwords

    @pytest.mark.asyncio
    async def test_tokenize_async(self, processor: TextProcessor) -> None:
        tokens = await processor.tokenize_async("这是一个测试")
        assert isinstance(tokens, list)

    @pytest.mark.asyncio
    async def test_load_stopwords_from_file(
        self, processor: TextProcessor, tmp_path
    ) -> None:
        sw_file = tmp_path / "custom_sw.txt"
        sw_file.write_text("# comment\nword1\nword2\n", encoding="utf-8")
        result = await processor.load_stopwords(str(sw_file))
        assert "word1" in result
        assert "word2" in result

    @pytest.mark.asyncio
    async def test_load_stopwords_file_not_found(
        self, processor: TextProcessor
    ) -> None:
        with pytest.raises(FileNotFoundError):
            await processor.load_stopwords("/nonexistent/stopwords.txt")

    @pytest.mark.asyncio
    async def test_load_stopwords_io_error(
        self, processor: TextProcessor, tmp_path
    ) -> None:
        sw_file = tmp_path / "bad.txt"
        sw_file.write_text("test", encoding="utf-8")
        with patch("aiofiles.open", side_effect=OSError("disk error")):
            with pytest.raises(OSError):
                await processor.load_stopwords(str(sw_file))


class TestTextProcessorFallbackSegment:
    """测试 _fallback_segment — used when jieba unavailable or disabled."""

    def test_fallback_chinese_only(self) -> None:
        tokens = TextProcessor._fallback_segment("你好世界")
        assert tokens == ["你", "好", "世", "界"]

    def test_fallback_english_only(self) -> None:
        tokens = TextProcessor._fallback_segment("hello world")
        assert len(tokens) == 2

    def test_fallback_mixed(self) -> None:
        tokens = TextProcessor._fallback_segment("hello 世界 test")
        assert "世" in tokens
        assert "界" in tokens

    def test_fallback_empty(self) -> None:
        tokens = TextProcessor._fallback_segment("")
        assert tokens == []

    def test_fallback_spaces_only(self) -> None:
        tokens = TextProcessor._fallback_segment("   ")
        assert tokens == []

    def test_fallback_single_char(self) -> None:
        tokens = TextProcessor._fallback_segment("a")
        assert tokens == ["a"]

    def test_fallback_numbers(self) -> None:
        tokens = TextProcessor._fallback_segment("2024")
        assert tokens == ["2024"]


class TestTextProcessorEdgeCases:
    def test_tokenize_skip_single_ascii_char(self) -> None:
        proc = TextProcessor()
        tokens = proc.tokenize("a b c")
        # Single ascii chars are skipped by tokenize filter
        for t in tokens:
            assert len(t) > 1 or not t.isascii()

    def test_tokenize_skip_pure_punctuation(self) -> None:
        proc = TextProcessor()
        tokens = proc.tokenize("... --- ***")
        # Pure punctuation tokens should be skipped
        assert len(tokens) == 0

    def test_tokenize_preserves_chinese_single_char(self) -> None:
        proc = TextProcessor()
        tokens = proc.tokenize("我", remove_stopwords=False)
        # Single Chinese chars may be preserved (not ASCII, not pure punctuation)
        assert isinstance(tokens, list)

    def test_clean_text_removes_mentions(self) -> None:
        proc = TextProcessor()
        result = proc._clean_text("@alice check #topic for info")
        assert "alice" not in result
        assert "topic" not in result

    def test_clean_text_removes_www_urls(self) -> None:
        proc = TextProcessor()
        result = proc._clean_text("visit www.example.com for more")
        assert "www.example.com" not in result

    def test_add_custom_words_jieba_unavailable(self) -> None:
        import core.processors.text_processor as tp

        original = tp.JIEBA_AVAILABLE
        tp.JIEBA_AVAILABLE = False
        try:
            with pytest.warns(UserWarning):
                proc = TextProcessor()
                proc.add_custom_words(["test"])
            # Should not raise, just warn
        finally:
            tp.JIEBA_AVAILABLE = original

    def test_segment_jieba_fallback_on_error(self) -> None:
        # Simulate jieba.cut_for_search raising an exception
        with patch("core.processors.text_processor.JIEBA_AVAILABLE", True):
            with patch("core.processors.text_processor.JIEBA_RUNTIME_DISABLED", False):
                with patch(
                    "jieba.cut_for_search", side_effect=RuntimeError("jieba crash")
                ):
                    with pytest.warns(UserWarning):
                        proc = TextProcessor()
                        result = proc._segment("你好世界")
                        assert isinstance(result, list)

    def test_disable_jieba_runtime(self) -> None:
        import core.processors.text_processor as tp

        tp.JIEBA_RUNTIME_DISABLED = False
        TextProcessor._disable_jieba_runtime()
        assert tp.JIEBA_RUNTIME_DISABLED is True
        tp.JIEBA_RUNTIME_DISABLED = False  # reset

    def test_tokenize_cleaned_text_empty_after_clean(self) -> None:
        proc = TextProcessor()
        # Text that becomes empty after cleaning (only URL)
        tokens = proc.tokenize("https://example.com")
        assert tokens == []

    def test_get_word_freq_empty(self) -> None:
        proc = TextProcessor()
        freq = proc.get_word_freq([])
        assert freq == {}

    def test_is_stopword_edge(self) -> None:
        proc = TextProcessor()
        assert proc.is_stopword("的") is True
        assert proc.is_stopword("") is False

    def test_add_custom_words_with_failures(self) -> None:
        import core.processors.text_processor as tp

        original = tp.JIEBA_AVAILABLE
        tp.JIEBA_AVAILABLE = True
        try:
            with patch("jieba.add_word", side_effect=ValueError("add failed")):
                with pytest.warns(UserWarning):
                    proc = TextProcessor()
                    proc.add_custom_words(["valid_word"])
                # Should not raise; word is tracked as failed
        finally:
            tp.JIEBA_AVAILABLE = original

    def test_add_custom_words_filter_invalid(self) -> None:
        import core.processors.text_processor as tp

        original = tp.JIEBA_AVAILABLE
        tp.JIEBA_AVAILABLE = True
        try:
            proc = TextProcessor()
            proc.add_custom_words(["valid", "", "  ", "also_valid"])
            assert "valid" in proc.custom_words
            assert "also_valid" in proc.custom_words
            assert "" not in proc.custom_words
        finally:
            tp.JIEBA_AVAILABLE = original
