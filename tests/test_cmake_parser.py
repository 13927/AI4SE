from __future__ import annotations

from aise.extractors.cmake import parse_cmakelists, parse_cmakelists_with_subdirs


def test_parse_cmakelists_targets_and_links():
    text = """
    cmake_minimum_required(VERSION 3.10)
    project(demo)
    add_library(foo foo.cc)
    add_executable(app main.cc)
    target_link_libraries(app PRIVATE foo pthread)
    """
    t = parse_cmakelists(text)
    assert "foo" in t and "app" in t
    assert t["app"].kind == "executable"
    assert "foo" in t["app"].links


def test_parse_cmakelists_subdirs():
    text = """
    add_subdirectory(googletest)
    add_subdirectory(googlemock)
    """
    _t, sub = parse_cmakelists_with_subdirs(text)
    assert "googletest" in sub and "googlemock" in sub
