# JBrowse prepare example
#
# # install jbrowse CLI, this might need sudo, or check the jbrowse docs if you don't have root access
# npm install -g @jbrowse/cli
#
# # create
# jbrowse create jbrowse2
#
# # add genome fasta
# jbrowse add-assembly /ref/mm10/fasta/with_chrl/mm10_with_chrl.fa.gz -l symlink --name mm10
#
# # add gene annotation gff
# awk '$1 ~ /^#/ {print $0;next} {print $0 | "sort -t\"\t\" -k1,1 -k4,4n"}' \
# gencode.vM23.annotation.name_as_id.gff3 > gencode.vM23.annotation.name_as_id.gff3.sorted.gff3
# bgzip gencode.vM23.annotation.name_as_id.gff3.sorted.gff3
# tabix gencode.vM23.annotation.name_as_id.gff3.sorted.gff3.gz
#
# a small GFF contain gene name only, a large GFF contain gene transcripts and exon/intron
# jbrowse add-track /ref/mm10/gencode/vm23/gencode.vM23.annotation.gff3.gz -a mm10 -l symlink -n gencode-vm23-genes
# jbrowse add-track /ref/mm10/gencode/vm23/gencode.vM23.annotation.name_as_id.gene_only.sorted.gff3.gz \
# -a mm10 -l symlink -n gencode-vm23-transcripts
#
# add text index to allow gene name and id search in the browser
# jbrowse text-index -a mm10


# TODO: use the Dash JBrowse component, check jbrowse-jupyter

import os
import pathlib
import subprocess


def _run_cmd(cmd):
    print(cmd)
    subprocess.run(cmd, shell=True, check=True)


class JBrowse:
    def __init__(self, path=None, config="config.json"):
        if path is None:
            path = os.getcwd()
            path = pathlib.Path(path) / "jbrowse2"
        self.path = pathlib.Path(path).absolute().resolve()
        self.config = self.path / config
        if self.config.exists():
            self.created = True
        else:
            self.created = False
        self._test_cli()

    @staticmethod
    def _test_cli():
        try:
            _run_cmd("jbrowse --version")
        except Exception as e:
            print(
                "JBrowse does not seem to be installed. "
                'Please install it first by running "npm install -g @jbrowse/cli; npm install -u serve"'
                "See details at https://jbrowse.org/jb2/docs/quickstart_cli/#installing-the-cli-tools"
            )
            raise e

    def _create(self):
        self._test_cli()

        if not self.path.exists():
            self.path.mkdir(parents=True, exist_ok=True)

        _run_cmd(f"jbrowse create {self.path}")

    def add_assembly(self, fasta_path, name=None, load="symlink"):
        jb_path = self.path / pathlib.Path(fasta_path).name
        if jb_path.exists():
            load = "inPlace"

        if name is None:
            name = pathlib.Path(fasta_path).stem
        _run_cmd(
            f"cd {self.path} && "
            f"jbrowse add-assembly {fasta_path} "
            f"--name {name} "
            f"--load {load} "
            f"--target {self.config}"
        )

    def add_track(self, track_path, name=None, load="symlink"):
        jb_path = self.path / pathlib.Path(track_path).name
        if jb_path.exists():
            load = "inPlace"

        if name is None:
            name = pathlib.Path(track_path).stem
        _run_cmd(
            f"cd {self.path} && "
            f"jbrowse add-track {track_path} "
            f"--name {name} "
            f"--load {load} "
            f"--target {self.config}"
        )

    def text_index(self):
        jb_path = self.path / "trix"
        if jb_path.exists():
            print(f"{jb_path} already exists. Skipping.")
            return

        _run_cmd(f"cd {self.path} && " f"jbrowse text-index {self.config}")

    def create(self, fasta_path, gene_gtf, transcript_gtf=None):
        if self.config.exists():
            print(f"{self.config} already exists. Skipping.")
            return
        self._create()
        self.add_assembly(fasta_path)
        self.add_track(gene_gtf)
        if transcript_gtf:
            self.add_track(transcript_gtf)
        self.text_index()

        self.created = True
        return

    def serve(self, port=3000):
        if not self.created:
            raise Exception("JBrowse not created yet. Please run create() first.")
        _run_cmd(f"cd {self.path} && npx serve -S --port {port} .")
        return
