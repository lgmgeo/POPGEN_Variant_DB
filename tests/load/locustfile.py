
from locust import HttpUser, task, between


class POPGENUser(HttpUser):
    # Pause entre les requêtes d'un même utilisateur simulé.
    wait_time = between(1, 2)

    @task(5)
    def health_check(self):
        """Vérifie la disponibilité de l'API."""
        self.client.get("/health", name="/health")

    @task(3)
    def search_by_id(self):
        """Recherche un identifiant de variant."""
        self.client.get(
            "/variants",
            params={"id": "rggDfaq0xkKv", "limit": 10},
            name="/variants?id",
        )

    @task(2)
    def search_by_region(self):
        """Recherche dans une petite région du chromosome 20."""
        self.client.get(
            "/variants",
            params={
                "chrom": "chr20",
                "start": 7000,
                "end": 8000,
                "limit": 10,
            },
            name="/variants?region",
        )

