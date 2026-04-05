from __future__ import annotations

from pathlib import Path

from aise.extractors.java_rest import extract


def test_java_rest_extract_get_mapping(tmp_path: Path):
    base = tmp_path / "src/main/java/com/example"
    base.mkdir(parents=True)
    (tmp_path / "pom.xml").write_text("<project></project>", encoding="utf-8")
    (base / "HelloController.java").write_text(
        """
        package com.example;
        import org.springframework.web.bind.annotation.RestController;
        import org.springframework.web.bind.annotation.GetMapping;
        @RestController
        public class HelloController {
          @GetMapping("/hello")
          public String hello() { return "ok"; }
        }
        """,
        encoding="utf-8",
    )
    apis = extract(tmp_path)
    assert any(a.get("protocol") == "http" and a.get("http", {}).get("path") == "/hello" for a in apis)

