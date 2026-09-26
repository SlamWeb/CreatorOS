"""选题条目级 CRUD：编辑/删除/直写调序的 HTTP 契约。

隔离临时库；不调模型、不生产。
"""
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web import create_app


def main() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        url = f"sqlite:///{(root / 'crud.db').as_posix()}"
        upgrade_database(url)
        database = Database(url)
        app = create_app(database=database, run_service=ContentRunService(database, output_root=root / "outputs"))
        with TestClient(app) as client:
            creator = client.post("/api/creators", json={"display_name": "CRUD 账号"}).json()
            series = client.post(f"/api/creators/{creator['id']}/series", json={"name": "选题实验室"}).json()
            for index, title in enumerate(("第一条", "第二条", "第三条"), start=1):
                created = client.post(f"/api/series/{series['id']}/queue", json={
                    "topics": [{"title": title}], "request_id": f"seed-{index}-deadbeef",
                })
                assert created.status_code == 201, created.text
            topics = client.get(f"/api/series/{series['id']}/topics").json()["items"]
            ids = [t["id"] for t in topics]
            assert [t["title"] for t in topics] == ["第一条", "第二条", "第三条"]

            # 编辑标题与简介。
            edited = client.patch(f"/api/topics/{ids[0]}", json={"title": "第一条（改）", "brief": "新切入点"})
            assert edited.status_code == 200, edited.text
            again = client.get(f"/api/series/{series['id']}/topics").json()["items"]
            assert again[0]["title"] == "第一条（改）" and again[0]["brief"] == "新切入点"
            assert client.patch(f"/api/topics/{ids[0]}", json={}).status_code == 409
            assert client.patch("/api/topics/nope", json={"title": "x"}).status_code == 404

            # 直写调序：完整顺序一次生效；缺项/外栏项目被拒绝。
            assert client.post(f"/api/series/{series['id']}/reorder", json={
                "ordered_topic_ids": [ids[2], ids[0], ids[1]]}).status_code == 200
            reordered = client.get(f"/api/series/{series['id']}/topics").json()["items"]
            assert [t["title"] for t in reordered] == ["第三条", "第一条（改）", "第二条"]
            assert client.post(f"/api/series/{series['id']}/reorder", json={
                "ordered_topic_ids": [ids[0]]}).status_code == 409
            assert client.post(f"/api/series/{series['id']}/reorder", json={
                "ordered_topic_ids": [*ids, "topic-foreign"]}).status_code == 409

            # 有生产记录的选题禁止删除；未生产的可删。
            runs = ContentRunService(database, output_root=root / "outputs")
            run = runs.create(ids[2])
            assert run is not None
            assert client.post(f"/api/topics/{ids[2]}/delete", json={}).status_code == 409
            assert client.post(f"/api/topics/{ids[1]}/delete", json={}).status_code == 200
            remaining = client.get(f"/api/series/{series['id']}/topics").json()["items"]
            assert [t["id"] for t in remaining] == [ids[2], ids[0]]
            assert client.post(f"/api/topics/{ids[1]}/delete", json={}).status_code == 404

            # 账号删除：名下有栏目拒绝；空账号可删；再删 404。
            assert client.post(f"/api/creators/{creator['id']}/delete", json={}).status_code == 409
            empty = client.post("/api/creators", json={"display_name": "空账号"}).json()
            assert client.post(f"/api/creators/{empty['id']}/delete", json={}).status_code == 200
            assert client.post(f"/api/creators/{empty['id']}/delete", json={}).status_code == 404
        database.close()
    print("topic_crud_smoke=passed")


if __name__ == "__main__":
    main()
