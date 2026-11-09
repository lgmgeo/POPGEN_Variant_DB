from locust import HttpUser, between, task


class POPGENUser(HttpUser):
    # Pause entre les requêtes d'un même utilisateur simulé.
    wait_time = between(1, 2)

    @task(1)
    def health_check(self):
        """Vérifie la disponibilité de l'API."""
        self.client.get("/health", name="/health")

    @task(10)
    def search_by_id(self):
        """Recherche un identifiant de variant."""
        self.client.get(
            "/variants",
            params={"id": "2p2vh0ZkpqYo"},
            name="/variants?id",
        )

    @task(10)
    def search_by_region(self):
        """Recherche dans une grande région du chromosome 21."""
        self.client.get(
            "/variants",
            params={
                "chrom": "chr21",
                "start": 5000000,
                "end": 8000000,
                "limit": 1000,
            },
            name="/variants?bigregion",
        )

    @task(10)
    def search_by_symbol(self):
        """Recherche par symbole de gène."""
        self.client.get(
            "/variants",
            params={
                "gene": "ABCC13",
                "limit": 1000,
            },
            name="/variants?gene",
        )

    @task(1)
    def database_info(self):
        """Retrieve the name of the database used by the API."""
        self.client.get("/database", name="/database")
