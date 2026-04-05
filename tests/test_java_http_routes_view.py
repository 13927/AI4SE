from __future__ import annotations

from pathlib import Path

from aise.codewiki_ops import _generate_java_http_routes_view


def test_generate_java_http_routes_view_minimal(tmp_path: Path):
    # minimal controller + service + repo with constructor injection
    (tmp_path / "src").mkdir()
    (tmp_path / "src/Ctrl.java").write_text(
        "import org.springframework.web.bind.annotation.RestController;\n"
        "@RestController\n"
        "public class Ctrl {\n"
        "  public Ctrl(Svc svc) {}\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src/Svc.java").write_text(
        "import org.springframework.stereotype.Service;\n"
        "@Service\n"
        "public class Svc {\n"
        "  public Svc(Repo repo) {}\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src/Repo.java").write_text(
        "import org.springframework.data.repository.CrudRepository;\n"
        "public interface Repo extends CrudRepository<Object, Object> {}\n",
        encoding="utf-8",
    )

    module_symbols = {
        "kind": "view.module_symbols",
        "version": "0.1",
        "root": "src",
        "modules": {
            "app/java": {
                "classes_truncated": False,
                "classes": [
                    {"name": "Ctrl", "package": None, "stereotype": "controller", "file": "src/Ctrl.java"},
                    {"name": "Svc", "package": None, "stereotype": "service", "file": "src/Svc.java"},
                    {"name": "Repo", "package": None, "stereotype": "repository", "file": "src/Repo.java"},
                ],
            }
        },
    }
    java_apis = [
        {
            "id": "api/java/http/get/x",
            "http": {
                "method": "GET",
                "path": "/x",
                "handler": {"file": "src/Ctrl.java", "symbol": "pkg.Ctrl"},
            },
        }
    ]
    view = _generate_java_http_routes_view(cwd=tmp_path, java_apis=java_apis, module_symbols_view=module_symbols)
    assert view["kind"] == "view.java_http_routes"
    assert view["routes"][0]["handler_class"] == "Ctrl"
    chain = view["routes"][0]["chain"]
    assert [n["role"] for n in chain][:3] == ["controller", "service", "repository"]

