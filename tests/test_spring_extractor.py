from __future__ import annotations

from pathlib import Path

from aise.extractors.spring import extract


def test_spring_extract_main(tmp_path: Path):
    p = tmp_path / "src/main/java/com/example"
    p.mkdir(parents=True)
    (tmp_path / "pom.xml").write_text("<project></project>", encoding="utf-8")
    (p / "App.java").write_text(
        """
        package com.example;
        import org.springframework.boot.autoconfigure.SpringBootApplication;
        @SpringBootApplication
        public class App {
          public static void main(String[] args) {}
        }
        """,
        encoding="utf-8",
    )
    res = extract(tmp_path)
    assert res.entrypoints
    assert "com.example.App" in res.entrypoints[0].match_value

