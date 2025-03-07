import json
import os
import time
import manifest
import util

from testflows.core import TestScenario, Name, When, Then, Given, And, main, Module
from testflows.asserts import error
from testflows.connect import Shell

import settings

current_dir = os.path.dirname(os.path.abspath(__file__))
max_retries = 15

shell = Shell()
namespace = settings.test_namespace
kubectl_cmd = settings.kubectl_cmd


def launch(command, ok_to_fail=False, ns=namespace, timeout=60):
    # Build command
    cmd = f"{kubectl_cmd} "
    cmd_args = command.split(" ")
    if ns is not None and ns != "" and ns != "--all-namespaces":
        cmd += f"{cmd_args[0]} --namespace={ns} "
    elif ns == "--all-namespaces":
        cmd += f"{cmd_args[0]} {ns} "
    else:
        cmd += f"{cmd_args[0]} "

    if len(cmd_args) > 1:
        cmd += " ".join(cmd_args[1:])

    # Run command
    cmd = shell(cmd, timeout=timeout)
    # Check command failure
    code = cmd.exitcode
    if not ok_to_fail:
        if code != 0:
            print("command failed, output:")
            print(cmd.output)
        assert code == 0, error()
    # Command test result
    return cmd.output if (code == 0) or ok_to_fail else ""


def delete_chi(chi, ns=namespace, wait = True):
    with When(f"Delete chi {chi}"):
        launch(f"delete chi {chi}", ns=ns, timeout=600)
        if wait:
            wait_objects(
                chi,
                {
                    "statefulset": 0,
                    "pod": 0,
                    "service": 0,
                    },
                ns,
                )


def delete_all_chi(ns=namespace):
    crds = launch("get crds -o=custom-columns=name:.metadata.name", ns=ns).splitlines()
    if "clickhouseinstallations.clickhouse.altinity.com" in crds:
        chis = get("chi", "", ns=ns)
        if "items" in chis:
            for chi in chis["items"]:
                # kubectl(f"patch chi {chi} --type=merge -p '\{\"metadata\":\{\"finalizers\": [null]\}\}'", ns = ns)
                delete_chi(chi["metadata"]["name"], ns)


def create_and_check(config, check, ns=namespace, timeout=600):
    config = util.get_full_path(config)
    chi_name = manifest.get_chi_name(config)

    if "apply_templates" in check:
        print("Need to apply additional templates")
        for t in check["apply_templates"]:
            print("Applying template:" + t)
            apply(util.get_full_path(t), ns=ns)
        time.sleep(5)

    apply(config, ns=ns, timeout=timeout)

    if "object_counts" in check:
        wait_objects(chi_name, check["object_counts"], ns=ns)

    if "pod_count" in check:
        wait_object("pod", "", label=f"-l clickhouse.altinity.com/chi={chi_name}", count=check["pod_count"], ns=ns)

    if "chi_status" in check:
        wait_chi_status(chi_name, check["chi_status"], ns=ns)
    else:
        wait_chi_status(chi_name, "Completed", ns=ns)

    if "pod_image" in check:
        check_pod_image(chi_name, check["pod_image"], ns=ns)

    if "pod_volumes" in check:
        check_pod_volumes(chi_name, check["pod_volumes"], ns=ns)

    if "pod_podAntiAffinity" in check:
        check_pod_antiaffinity(chi_name, ns=ns)

    if "pod_ports" in check:
        check_pod_ports(chi_name, check["pod_ports"], ns=ns)

    if "service" in check:
        check_service(check["service"][0], check["service"][1], ns=ns)

    if "configmaps" in check:
        check_configmaps(chi_name, ns=ns)

    if "do_not_delete" not in check:
        delete_chi(chi_name, ns=ns)


def get(kind, name, label="", ns=namespace):
    out = launch(f"get {kind} {name} {label} -o json", ns=ns)
    return json.loads(out.strip())


def create_ns(ns):
    launch(f"create ns {ns}", ns=None)
    launch(f"get ns {ns}", ns=None)


def delete_ns(ns, ok_to_fail=False, timeout=600):
    launch(f"delete ns {ns}", ns=None, ok_to_fail=ok_to_fail, timeout=timeout)


def get_count(kind, name="", label="", ns=namespace):
    out = launch(f"get {kind} {name} -o=custom-columns=kind:kind,name:.metadata.name {label}", ns=ns, ok_to_fail=True)
    if (out is None) or (len(out) == 0):
        return 0
    return len(out.splitlines()) - 1


def count_objects(label="", ns=namespace):
    return {
        "statefulset": get_count("sts", ns=ns, label=label),
        "pod": get_count("pod", ns=ns, label=label),
        "service": get_count("service", ns=ns, label=label),
    }


def apply(config, ns=namespace, validate=True, timeout=30):
    with When(f"{config} is applied"):
        launch(f"apply --validate={validate} -f {config}", ns=ns, timeout=timeout)


def delete(config, ns=namespace, timeout=30):
    with When(f"{config} is deleted"):
        launch(f"delete -f {config}", ns=ns, timeout=timeout)


def wait_objects(chi, object_counts, ns=namespace):
    with Then(
            f"Waiting for: "
            f"{object_counts['statefulset']} statefulsets, "
            f"{object_counts['pod']} pods and "
            f"{object_counts['service']} services "
            f"to be available"
    ):
        for i in range(1, max_retries):
            cur_object_counts = count_objects(label=f"-l clickhouse.altinity.com/chi={chi}", ns=ns)
            if cur_object_counts == object_counts:
                break
            with Then(
                    f"Not ready yet. [ "
                    f"statefulset: {cur_object_counts['statefulset']} "
                    f"pod: {cur_object_counts['pod']} "
                    f"service: {cur_object_counts['service']} ]. "
                    f"Wait for {i * 5} seconds"
            ):
                time.sleep(i * 5)
        assert cur_object_counts == object_counts, error()


def wait_object(kind, name, label="", count=1, ns=namespace, retries=max_retries, backoff = 5):
    with Then(f"{count} {kind}(s) {name} should be created"):
        for i in range(1, retries):
            cur_count = get_count(kind, ns=ns, name=name, label=label)
            if cur_count >= count:
                break
            with Then("Not ready. Wait for " + str(i * backoff) + " seconds"):
                time.sleep(i * backoff)
        assert cur_count >= count, error()


def wait_command(command, result, count=1, ns=namespace, retries=max_retries):
    with Then(f"{command} should return {result}"):
        for i in range(1, retries):
            res = launch(command, ok_to_fail=True, ns=ns)
            if res == result:
                break
            with Then("Not ready. Wait for " + str(i * 5) + " seconds"):
                time.sleep(i * 5)
        assert res == result, error()


def wait_chi_status(chi, status, ns=namespace, retries=max_retries):
    wait_field("chi", chi, ".status.status", status, ns, retries)


def get_chi_status(chi, ns=namespace):
    get_field("chi", chi, ".status.status", ns)


def wait_pod_status(pod, status, ns=namespace):
    wait_field("pod", pod, ".status.phase", status, ns)


def wait_field(kind, name, field, value, ns=namespace, retries=max_retries, backoff = 5):
    with Then(f"{kind} {name} {field} should be {value}"):
        for i in range(1, retries):
            cur_value = get_field(kind, name, field, ns)
            if cur_value == value:
                break
            with Then("Not ready. Wait for " + str(i * backoff) + " seconds"):
                time.sleep(i * backoff)
        assert cur_value == value, error()


def wait_jsonpath(kind, name, field, value, ns=namespace, retries=max_retries):
    with Then(f"{kind} {name} -o jsonpath={field} should be {value}"):
        for i in range(1, retries):
            cur_value = get_jsonpath(kind, name, field, ns)
            if cur_value == value:
                break
            with Then("Not ready. Wait for " + str(i * 5) + " seconds"):
                time.sleep(i * 5)
        assert cur_value == value, error()


def get_field(kind, name, field, ns=namespace):
    out = launch(f"get {kind} {name} -o=custom-columns=field:{field}", ns=ns).splitlines()
    return out[1]


def get_jsonpath(kind, name, field, ns=namespace):
    out = launch(f"get {kind} {name} -o jsonpath=\"{field}\"", ns=ns).splitlines()
    return out[0]


def get_default_storage_class(ns=namespace):
    out = launch(
        f"get storageclass "
        f"-o=custom-columns="
        f"DEFAULT:\".metadata.annotations.storageclass\.kubernetes\.io/is-default-class\",NAME:.metadata.name",
        ns=ns,
    ).splitlines()
    for line in out[1:]:
        if line.startswith("true"):
            parts = line.split(maxsplit=1)
            return parts[1].strip()
    out = launch(
        f"get storageclass "
        f"-o=custom-columns="
        f"DEFAULT:\".metadata.annotations.storageclass\.beta\.kubernetes\.io/is-default-class\",NAME:.metadata.name",
        ns=ns,
    ).splitlines()
    for line in out[1:]:
        if line.startswith("true"):
            parts = line.split(maxsplit=1)
            return parts[1].strip()


def get_pod_spec(chi_name, pod_name="", ns=namespace):
    label = f"-l clickhouse.altinity.com/chi={chi_name}"
    if pod_name == "":
        pod = get("pod", "", ns=ns, label=label)["items"][0] 
    else:
        pod = get("pod", pod_name, ns=ns)
    return pod["spec"]


def get_pod_image(chi_name, pod_name="", ns=namespace):
    pod_image = get_pod_spec(chi_name, pod_name, ns)["containers"][0]["image"]
    return pod_image


def get_pod_names(chi_name, ns=namespace):
    pod_names = launch(
        f"get pods -o=custom-columns=name:.metadata.name -l clickhouse.altinity.com/chi={chi_name}",
        ns=ns,
    ).splitlines()
    return pod_names[1:]


def get_pod_volumes(chi_name, pod_name="",  ns=namespace):
    volume_mounts = get_pod_spec(chi_name, pod_name, ns)["containers"][0]["volumeMounts"]
    return volume_mounts


def get_pod_ports(chi_name, pod_name="", ns=namespace):
    port_specs = get_pod_spec(chi_name, pod_name, ns)["containers"][0]["ports"]
    ports = []
    for p in port_specs:
        ports.append(p["containerPort"])
    return ports


def check_pod_ports(chi_name, ports, ns=namespace):
    pod_ports = get_pod_ports(chi_name, ns=ns)
    with Then(f"Expect pod ports {pod_ports} to match {ports}"):
        assert sorted(pod_ports) == sorted(ports)


def check_pod_image(chi_name, image, ns=namespace):
    pod_image = get_pod_image(chi_name, ns=ns)
    with Then(f"Expect pod image {pod_image} to match {image}"):
        assert pod_image == image


def check_pod_volumes(chi_name, volumes, ns=namespace):
    pod_volumes = get_pod_volumes(chi_name, ns=ns)
    for v in volumes:
        with Then(f"Expect pod has volume mount {v}"):
            found = 0
            for vm in pod_volumes:
                if vm["mountPath"] == v:
                    found = 1
                    break
            assert found == 1


def get_pvc_size(pvc_name, ns=namespace):
    return get_field("pvc", pvc_name, ".spec.resources.requests.storage", ns)


def check_pod_antiaffinity(chi_name, pod_name = "", match_labels = {}, topologyKey = "kubernetes.io/hostname", ns=namespace):
    pod_spec = get_pod_spec(chi_name, pod_name, ns)
    if match_labels == {}:
        match_labels = {
                        "clickhouse.altinity.com/app": "chop",
                        "clickhouse.altinity.com/chi": f"{chi_name}",
                        "clickhouse.altinity.com/namespace": f"{ns}",
                    }
    expected = {
        "requiredDuringSchedulingIgnoredDuringExecution": [
            {
                "labelSelector": {
                    "matchLabels": match_labels,
                },
                "topologyKey": f"{topologyKey}",
            },
        ],
    }
    with Then(f"Expect podAntiAffinity to exist and match {expected}"):
        assert "affinity" in pod_spec
        assert "podAntiAffinity" in pod_spec["affinity"]
        assert pod_spec["affinity"]["podAntiAffinity"] == expected


def check_service(service_name, service_type, ns=namespace):
    with When(f"{service_name} is available"):
        service = get("service", service_name, ns=ns)
        with Then(f"Service type is {service_type}"):
            assert service["spec"]["type"] == service_type


def check_configmaps(chi_name, ns=namespace):
    check_configmap(
        f"chi-{chi_name}-common-configd",
        [
            "01-clickhouse-01-listen.xml",
            "01-clickhouse-02-logger.xml",
            "01-clickhouse-03-query_log.xml",
        ],
        ns=ns,
    )

    check_configmap(
        f"chi-{chi_name}-common-usersd",
        [
            "01-clickhouse-user.xml",
            "02-clickhouse-default-profile.xml",
        ],
        ns=ns,
    )


def check_configmap(cfg_name, values, ns=namespace):
    cfm = get("configmap", cfg_name, ns=ns)
    for v in values:
        with Then(f"{cfg_name} should contain {v}"):
            assert v in cfm["data"]
