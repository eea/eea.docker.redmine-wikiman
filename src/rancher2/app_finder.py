class Rancher2AppFinder:
    def _find_apps_by_service_location(self, service_location):
        return []

    def _find_apps_by_chart_name(self, chart_name):
        return []

    def find(self, service_location=None, chart_name=None):
        found_apps = []

        if service_location:
            found_apps = self._find_apps_by_service_location(service_location)

        if chart_name:
            found_apps = self._find_apps_by_chart_name(chart_name)

        return found_apps
